"""Project skill discovery and selected-body loading from standard SKILL.md files."""
from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
import json
from pathlib import Path
import re

import yaml

from turbine_kg.runtime import file_hash
from turbine_kg.settings import PROJECT_ROOT


def _frontmatter(path: Path) -> dict:
    # Discovery decodes only metadata. The opaque full-file hash below pins body
    # changes too; instructions enter model context only after explicit selection.
    with path.open(encoding='utf-8-sig') as stream:
        if stream.readline().strip() != '---':
            raise ValueError(f'Skill缺少YAML frontmatter: {path.name}')
        lines=[]
        for line in stream:
            if line.strip() == '---':
                value=yaml.safe_load(''.join(lines))
                if not isinstance(value,dict):raise ValueError('Skill frontmatter须为对象')
                return value
            lines.append(line)
    raise ValueError('Skill frontmatter未结束')


class SkillCatalog(Sequence):
    """A metadata-only catalog with immutable content versions for one answer."""
    def __init__(self, paths: list[tuple[str | None, Path]], registry=None):
        self._descriptors=[];self._paths={};self._loaded={}
        for registered_id,path in paths:
            front=_frontmatter(path)
            name=front.get('name');description=front.get('description');metadata=front.get('metadata',{})
            if not isinstance(name,str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',name) or len(name)>64 or path.parent.name!=name:
                raise ValueError('Skill名称须与目录一致，使用小写字母、数字和连字符')
            if not isinstance(description,str) or not description.strip() or not isinstance(metadata,dict):
                raise ValueError('Skill须提供简介和metadata')
            identifier=metadata.get('id',name)
            if not isinstance(identifier,str) or identifier in self._paths or registered_id not in (None,identifier):
                raise ValueError('Skill注册ID重复或与frontmatter不一致')
            version=metadata.get('version');items=metadata.get('required_items',metadata.get('knowledge_items'));relations=metadata.get('relation_ids')
            if not isinstance(version,(str,int)) or isinstance(version,bool) or not str(version).strip():raise ValueError('Skill版本无效')
            if not isinstance(items,list) or not items or any(not isinstance(item,str) for item in items):raise ValueError('Skill知识项须为字符串列表')
            if not isinstance(relations,list) or not relations or any(not isinstance(item,str) for item in relations):raise ValueError('Skill关系须为字符串列表')
            descriptor={'id':identifier,'name':name,'version':str(version),'description':description.strip(),
                        'required_items':list(dict.fromkeys(items)),'relation_ids':list(dict.fromkeys(relations)),
                        'content_hash':file_hash(path)}
            if registry is not None:registry.validate_skill(descriptor)
            self._descriptors.append(descriptor);self._paths[identifier]=path

    def __len__(self):return len(self._descriptors)

    def __getitem__(self,index):return deepcopy(self._descriptors[index])

    def versions(self) -> tuple[str,...]:
        return tuple(f"{row['id']}:{row['version']}:{row['content_hash']}" for row in self._descriptors)

    def load_selected(self, ids: list[str]) -> list[dict]:
        selected=[]
        for identifier in dict.fromkeys(ids):
            descriptor=next((row for row in self._descriptors if row['id']==identifier),None)
            if descriptor is None:raise ValueError('未知Skill ID: '+str(identifier))
            path=self._paths[identifier]
            if file_hash(path)!=descriptor['content_hash']:raise ValueError('Skill内容在本次固定版本后改变，须重新开始问答')
            if identifier not in self._loaded:
                raw=path.read_text(encoding='utf-8-sig')
                match=re.match(r'\A---\s*\r?\n.*?\r?\n---\s*(?:\r?\n|\Z)',raw,re.S)
                body=raw[match.end():].strip() if match else ''
                if not body:raise ValueError('所选Skill正文为空: '+identifier)
                self._loaded[identifier]={**descriptor,'instructions':body}
            selected.append(deepcopy(self._loaded[identifier]))
        return selected


def load_catalog(skill_root: Path | None = None, *, registry=None, manifest: Path | None = None) -> SkillCatalog:
    """Read only metadata from the project's declared skill packages."""
    default_root=skill_root is None
    root=(skill_root or PROJECT_ROOT/'skills').resolve()
    if manifest is None and default_root:manifest=PROJECT_ROOT/'config/skills.json'
    if manifest is not None:
        records=json.loads(manifest.read_text(encoding='utf-8'))
        paths=[]
        for row in records:
            path=(root/row['path']).resolve()
            if not path.is_relative_to(root) or path.name!='SKILL.md':raise ValueError('Skill注册路径须位于项目skills范围内')
            paths.append((row['id'],path))
    else:
        paths=[(None,directory/'SKILL.md') for directory in sorted(root.iterdir()) if directory.is_dir() and (directory/'SKILL.md').is_file()]
    return SkillCatalog(paths,registry=registry)


def task_skill_context(skills, tasks) -> list[dict]:
    """Share complete selected bodies only with tasks using that knowledge item."""
    selected=[]
    for skill in skills or []:
        if not isinstance(skill,dict) or not isinstance(skill.get('instructions'),str) or not skill['instructions'].strip():continue
        own=[task for task in tasks or [] if skill.get('id') in task.get('skill_ids',[]) and task.get('knowledge_item') in skill.get('required_items',[])]
        if not own:continue
        selected.append({'id':skill['id'],'version':skill['version'],'instructions':skill['instructions'],
                         'task_ids':list(dict.fromkeys(task['id'] for task in own)),
                         'knowledge_items':list(dict.fromkeys(task['knowledge_item'] for task in own))})
    return selected
