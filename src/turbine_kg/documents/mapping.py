"""Verify page correspondences by page pixels; never assume matching indices."""
from __future__ import annotations
import cv2
import pymupdf as fitz
import numpy as np


def thumbnail(page):
    pix = page.get_pixmap(matrix=fitz.Matrix(0.2, 0.2), colorspace=fitz.csGRAY, alpha=False)
    return cv2.resize(np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width), (160, 160)).astype(np.float32)


class PageMapper:
    def __init__(self, original, derived):
        self.original, self.derived = original, derived
        self.original_images = [thumbnail(page) for page in original]
        self.mapping = {}
        self.unmapped_derived_pages = []
        if derived:
            # Normalize once. Repeated corrcoef allocations were quadratic in pages.
            vectors = np.asarray([image.reshape(-1) for image in self.original_images])
            centered = vectors - vectors.mean(axis=1, keepdims=True)
            norms = np.linalg.norm(centered, axis=1)
            normalized = centered / np.maximum(norms[:, None], 1)
            for page in derived:
                candidate = thumbnail(page)
                matches = []
                for turn in range(4):
                    rotated = np.rot90(candidate, turn)
                    if rotated.std() < 2: continue
                    vector = rotated.reshape(-1) - rotated.mean()
                    scores = normalized @ (vector / np.linalg.norm(vector))
                    matches.extend((min(1.0,max(-1.0,float(score))), index, turn) for index, score in enumerate(scores) if norms[index] > 320)
                matches.sort(reverse=True)
                if matches and matches[0][0] >= 0.985 and (len(matches) == 1 or matches[0][0] - matches[1][0] >= 0.012):
                    score, index, turn = matches[0]
                    if index not in self.mapping:
                        self.mapping[index] = {'derived_page': page.number + 1, 'rotation_quarters_ccw': turn, 'pixel_correlation': score, 'status': 'verified_pixel_match'}
                    else: self.mapping[index] = {'status': 'ambiguous', 'candidate_derived_pages': [self.mapping[index].get('derived_page'), page.number + 1]}
                else:
                    self.unmapped_derived_pages.append(page.number + 1)

    def display_box(self, bbox, original_index, *, derived=False, already_display=False):
        original = self.original[original_index]
        if not derived: return list(fitz.Rect(bbox) if already_display else fitz.Rect(bbox) * original.rotation_matrix)
        record = self.mapping.get(original_index, {})
        if record.get('status') != 'verified_pixel_match': raise ValueError('OCR原页坐标映射未验证')
        page = self.derived[record['derived_page'] - 1]
        box = fitz.Rect(bbox) if already_display else fitz.Rect(bbox) * page.rotation_matrix
        points = [(x / page.rect.width, y / page.rect.height) for x,y in ((box.x0,box.y0),(box.x1,box.y0),(box.x1,box.y1),(box.x0,box.y1))]
        for _ in range(record['rotation_quarters_ccw']): points = [(y, 1 - x) for x, y in points]
        xs, ys = zip(*points)
        return [min(xs) * original.rect.width, min(ys) * original.rect.height, max(xs) * original.rect.width, max(ys) * original.rect.height]

    def processing_box(self, original_display_bbox, original_index):
        record = self.mapping.get(original_index,{})
        if record.get('status') != 'verified_pixel_match': raise ValueError('OCR原页坐标映射未验证')
        original = self.original[original_index]
        page = self.derived[record['derived_page']-1]
        box = fitz.Rect(original_display_bbox)
        points = [(x/original.rect.width,y/original.rect.height) for x,y in ((box.x0,box.y0),(box.x1,box.y0),(box.x1,box.y1),(box.x0,box.y1))]
        for _ in range(record['rotation_quarters_ccw']): points=[(1-y,x) for x,y in points]
        xs,ys=zip(*points)
        return fitz.Rect(min(xs)*page.rect.width,min(ys)*page.rect.height,max(xs)*page.rect.width,max(ys)*page.rect.height) * page.derotation_matrix
