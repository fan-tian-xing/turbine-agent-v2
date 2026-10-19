"""The ontology contract for extraction, validation and graph navigation."""
from __future__ import annotations
import json
import re
import unicodedata
from pathlib import Path
from rdflib import Graph, Namespace, RDF, RDFS, OWL, SKOS, Literal, BNode, URIRef
from rdflib.collection import Collection
from turbine_kg.runtime import fingerprint, identity
from turbine_kg.settings import PROJECT_ROOT


def normalized(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).lower().split())


class OntologyRegistry:
    def __init__(self, path: Path | None = None):
        self.data = json.loads((path or PROJECT_ROOT / "config/ontology_registry.json").read_text(encoding="utf-8"))
        self.version = fingerprint(self.data)
        self.business_classes = {row["id"]: row for row in self.data["classes"]}
        self.record_types = {row["id"]: row for row in self.data.get("record_types", [])}
        self.classes = {**self.business_classes, **self.record_types}
        self.domain_classes = {row["id"]: row for row in self.data.get("domain_classes", [])}
        self.relations = {row["id"]: row for row in self.data["relations"]}
        self.roles = {row["id"]: row for row in self.data["statement_roles"]}
        self.domain_aliases = {}
        for row in [*self.business_classes.values(), *self.domain_classes.values()]:
            for alias in [row.get("label", row.get("zh")), *row.get("aliases", [])]:
                key = normalized(alias)
                if key in self.domain_aliases and self.domain_aliases[key] != row["id"]:
                    raise ValueError("本体类别名有歧义：" + alias)
                self.domain_aliases[key] = row["id"]
        self._validate_contract()

    @classmethod
    def for_version(cls, version: str) -> "OntologyRegistry":
        """Load the exact ontology contract pinned by the document record."""
        current = cls()
        if version == current.version:
            return current
        if not re.fullmatch(r"[0-9a-f]{64}", version):
            raise ValueError("本体版本标识无效")
        archive_root = PROJECT_ROOT / "config" / "ontology_versions"
        index_path = archive_root / "index.json"
        try:
            archive_index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("本体历史合同索引不可用") from error
        filename = archive_index.get(version)
        if not isinstance(filename, str) or Path(filename).name != filename or Path(filename).suffix != ".json":
            raise ValueError("本体历史合同索引缺少版本：" + version)
        archived = archive_root / filename
        if not archived.is_file():
            raise ValueError("缺少资料记录对应的本体合同：" + version)
        registry = cls(archived)
        if registry.version != version:
            raise ValueError("本体历史合同内容与版本不符")
        return registry

    def domain_class_by_id(self, class_id: str) -> dict | None:
        if class_id == "Thing":
            return {"id": identity("ontology-class", class_id), "class_id": class_id,
                    "label": "事物", "definition": "本体的逻辑顶层，所有类均为其子类。",
                    "aliases": [], "parent_class_id": None, "parent_class_ids": [],
                    "class_mapping": {"rdf": str(OWL.Thing)}}
        row = self.domain_classes.get(class_id)
        if row is not None:
            return {**row, "class_id": class_id, "parent_class_id": row["parent"],
                    "parent_class_ids": [row["parent"], *row.get("additional_parents", [])],
                    "id": identity("ontology-class", class_id)}
        upper = self.business_classes.get(class_id)
        if upper is not None:
            return {"id": identity("ontology-class", class_id), "class_id": class_id,
                    "label": upper["zh"], "definition": upper["definition"],
                    "aliases": upper.get("aliases", []), "scope_note": upper.get("scope_note"),
                    "definition_sources": upper.get("definition_sources", []),
                    "parent_class_id": upper["parent"], "parent_class_ids": [upper["parent"]]}
        return None

    def domain_class(self, surface: str) -> dict | None:
        class_id = self.domain_aliases.get(normalized(surface))
        return self.domain_class_by_id(class_id) if class_id else None

    def qualified_class(self, surface: str) -> dict | None:
        """Resolve an explicit object + kind name only when the split is unique."""
        name = normalized(surface)
        matches = set()
        for class_id, row in self.domain_classes.items():
            for suffix in [row["label"], *row.get("aliases", [])]:
                kind = normalized(suffix)
                if not kind or name == kind or not name.endswith(kind):
                    continue
                qualifier = self.domain_class(name[:-len(kind)])
                if qualifier and self.domain_is_a(qualifier["class_id"], "PhysicalObject"):
                    matches.add(class_id)
        return self.domain_class_by_id(next(iter(matches))) if len(matches) == 1 else None

    def resolve_entity_class(self, surface: str, *, upper_class: str | None = None) -> dict | None:
        """Resolve a source label to one reviewed class without guessing.

        The resolver accepts an exact registered name/alias, a unique
        object-plus-kind compound, or a unique registered kind at the end of a
        longer source label (for example an activity name).  It does not use
        co-occurrence or arbitrary substring matches, so an unresolved Mention
        remains available when the source does not justify a class.
        """
        if not isinstance(surface, str) or not surface.strip():
            return None
        exact = self.domain_class(surface)
        qualified = self.qualified_class(surface)
        candidates = {row['class_id']: row for row in (exact, qualified) if row is not None}
        if not candidates:
            suffixes = {}
            surface_key = normalized(surface)
            for row in [*self.business_classes.values(), *self.domain_classes.values()]:
                class_id = row['id']
                names = [row.get('label', row.get('zh')), *row.get('aliases', [])]
                for name in names:
                    name_key = normalized(name)
                    if len(name_key) >= 2 and surface_key.endswith(name_key) and surface_key != name_key:
                        suffixes[class_id] = self.domain_class_by_id(class_id)
            if len(suffixes) == 1:
                candidates = suffixes
        if len(candidates) != 1:
            return None
        resolved = next(iter(candidates.values()))
        if upper_class and upper_class != 'Mention' and not self.domain_is_a(resolved['class_id'], upper_class):
            return None
        return resolved

    def domain_is_a(self, class_id: str, upper_class: str) -> bool:
        """Check a reviewed kind against either a domain ancestor or an upper class."""
        if class_id in self.business_classes:
            return self.is_a(class_id, upper_class)
        if class_id not in self.domain_classes and class_id not in self.business_classes:
            return False
        pending, seen = [class_id], set()
        while pending:
            current = pending.pop()
            if current in seen: continue
            seen.add(current)
            if current == upper_class:
                return True
            if current in self.domain_classes:
                row = self.domain_classes[current]
                pending.extend([row["parent"], *row.get("additional_parents", [])])
            elif self.is_a(current, upper_class):
                return True
        return False

    def ontology_binding(self, label: str, class_id: str, supports: list[dict], supplied: dict) -> dict:
        """Resolve a source mention with an exact or uniquely typed name.

        A qualified or unique suffix name is accepted only when the same label
        occurs in a supported source member. Mere co-occurrence in a section
        never suffices.
        """
        domain = self.domain_class_by_id(class_id)
        if domain is None or class_id == "Thing":
            raise ValueError("本体类别标识未注册")
        surface = normalized(label)
        mentions, headings = [], []
        for support in supports:
            eid = support["evidence_id"]
            view = supplied.get(eid)
            if view is None:
                raise ValueError("本体类别缺少来源证据")
            selected = set(support["member_keys"])
            for member in view.get("members", []):
                quote = member.get("quote", "")
                key = member.get("member_key")
                source_role = member.get("role")
                local_lead = source_role == "section" and member.get("source_kind") in ("paragraph", "text", "clause", "list_item")
                if key in selected and (source_role in ("content", "value", "row_label", "note") or local_lead) and surface in normalized(quote):
                    mentions.append({"evidence_id": eid, "member_key": key})
                if member.get("role") == "section" and isinstance(quote, str):
                    heading = re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", quote).strip()
                    if heading:
                        headings.append({"evidence_id": eid, "member_key": key,
                                         "heading_object": heading})
        if not mentions:
            raise ValueError("本体类别简称未在陈述支持成员中出现")
        names = [domain["label"], *domain.get("aliases", [])]
        if any(normalized(name) == surface for name in names):
            return {"method": "exact_source_name", "class_id": class_id,
                    "surface_label": label, "source_members": mentions}
        # A source compound may qualify a reusable part/activity kind with a
        # separately registered object. The qualifier is retained as context,
        # never added as an alias or collapsed into a new compound class.
        qualified = []
        for name in names:
            suffix = normalized(name)
            if suffix and surface.endswith(suffix) and surface != suffix:
                prefix = surface[:-len(suffix)]
                context_id = self.domain_aliases.get(prefix)
                if context_id and self.domain_is_a(context_id, "PhysicalObject"):
                    qualified.append(context_id)
        if len(set(qualified)) == 1:
            context_id = qualified[0]
            return {"method": "qualified_source_name", "class_id": class_id,
                    "surface_label": label, "source_members": mentions,
                    "context_class_id": context_id,
                    "context_label": self.domain_class_by_id(context_id)["label"]}
        exact_class = self.domain_class(label)
        if exact_class is None or exact_class['class_id'] == class_id:
            suffix_ids = set()
            for candidate_id, row in [*self.business_classes.items(), *self.domain_classes.items()]:
                names_for_candidate = [row.get('label', row.get('zh')), *row.get('aliases', [])]
                if any(normalized(name) and surface.endswith(normalized(name)) and surface != normalized(name)
                       for name in names_for_candidate):
                    suffix_ids.add(candidate_id)
            if suffix_ids == {class_id}:
                return {'method': 'suffix_source_name', 'class_id': class_id,
                        'surface_label': label, 'source_members': mentions}
        mentioned_evidence = {member["evidence_id"] for member in mentions}
        matches = [{**heading, "class_name": name} for heading in headings for name in names
                   if heading["evidence_id"] in mentioned_evidence
                   and normalized(name) == normalized(heading["heading_object"]) + surface]
        # A title is a qualifier only when exact composition identifies one
        # registered class. Similar names and broad section co-occurrence fail.
        possible = {candidate_id for candidate_id, row in self.domain_classes.items()
                    for heading in headings for name in [row["label"], *row.get("aliases", [])]
                    if heading["evidence_id"] in mentioned_evidence
                    and normalized(name) == normalized(heading["heading_object"]) + surface}
        if not matches or possible != {class_id}:
            raise ValueError("本体类别简称缺少唯一且明确的标题对象支持")
        match = matches[0]
        return {"method": "heading_object_compound", "class_id": class_id,
                "surface_label": label, "source_members": mentions,
                "heading_member": {key: match[key] for key in ("evidence_id", "member_key", "heading_object")}}

    def relationship_type(self, relation_id: str) -> str:
        """Map a registered business relation to one safe Neo4j edge type."""
        row = self.relations.get(relation_id)
        if not row or not row.get('extractable') or not re.fullmatch(r'[a-z][a-z0-9_]*', relation_id):
            raise ValueError('未注册可存储的业务关系')
        return relation_id.upper()

    def _validate_contract(self):
        if (len(self.classes) != len(self.data["classes"]) + len(self.data.get("record_types", []))
                or len(self.domain_classes) != len(self.data.get("domain_classes", []))
                or len(self.relations) != len(self.data["relations"])):
            raise ValueError("注册表机器标识重复")
        if "Thing" in self.classes:
            raise ValueError("逻辑顶层必须使用 owl:Thing")
        for key in self.business_classes:
            seen, current = set(), key
            while current != "Thing":
                if current in seen or current not in self.business_classes:
                    raise ValueError("本体类层级无效：" + key)
                seen.add(current); current = self.classes[current]["parent"]
        if set(self.domain_classes) & set(self.classes):
            raise ValueError("领域类与上层类标识重复")
        for key, row in self.domain_classes.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key):
                raise ValueError("领域类机器标识无效：" + key)
            if not isinstance(row.get("label"), str) or not row["label"].strip() or not isinstance(row.get("definition"), str) or not row["definition"].strip():
                raise ValueError("领域类缺少名称或定义：" + key)
            additional = row.get("additional_parents", [])
            if (not isinstance(additional, list) or any(not isinstance(parent, str) for parent in additional)
                    or len(set([row["parent"], *additional])) != 1 + len(additional)):
                raise ValueError("领域类父类无效：" + key)
        def visit(current, path):
            if current in path: raise ValueError("领域类层级有环：" + current)
            if current not in self.domain_classes:
                if current not in self.business_classes:
                    raise ValueError("领域类父类无效：" + current)
                return
            row = self.domain_classes[current]
            for parent in [row["parent"], *row.get("additional_parents", [])]:
                visit(parent, path | {current})
        for key in self.domain_classes: visit(key, set())
        for row in self.relations.values():
            if not row["domain"] or not row["range"] or any(key not in self.classes for key in row["domain"] + row["range"]):
                raise ValueError("关系端点类别未注册：" + row["id"])
            if any(role not in self.roles for role in row.get("statement_roles", [])):
                raise ValueError("关系角色未注册：" + row["id"])
            for source, target in row.get("endpoint_pairs", []):
                if source not in row["domain"] or target not in row["range"]:
                    raise ValueError("关系端点组合无效：" + row["id"])

    def is_a(self, actual: str, allowed: str | list[str]) -> bool:
        allowed = [allowed] if isinstance(allowed, str) else allowed
        if actual not in self.classes:
            return False
        while actual != "Thing":
            if actual in allowed:
                return True
            parent = self.classes[actual].get("parent")
            if parent is None:
                return "Thing" in allowed
            actual = parent
        return "Thing" in allowed

    def canonical_label(self, surface: str) -> str:
        domain = self.domain_class(surface)
        return domain["label"] if domain else surface.strip()

    def expand(self, text: str) -> list[str]:
        found = []
        for row in [*self.business_classes.values(), *self.domain_classes.values()]:
            names = [row.get("label", row.get("zh")), *row.get("aliases", [])]
            if any(normalized(name) in normalized(text) for name in names):
                found.extend(names)
        return list(dict.fromkeys(found))

    def navigation(self, types: list[str], *, roles: list[str] | None = None) -> list[str]:
        """Outgoing statement links; hierarchy is a separate recall operation."""
        return [key for key, row in self.relations.items()
                if row["navigation"] and set(types) & set(row.get("statement_types", []))
                and (roles is None or set(roles) & set(row.get("statement_roles", [])))]

    @property
    def statement_types(self) -> list[str]:
        return list(self.data["statement_types"])

    def validate_entity(self, entity: dict) -> None:
        if not isinstance(entity.get("label"), str) or not entity["label"].strip(): raise ValueError("实体名称为空")
        if not any(character.isalnum() for character in entity['label']): raise ValueError("标点或版面碎片不能作为实体")
        row = self.classes.get(entity.get("class"))
        if not row or not row.get("extractable", False) or entity.get("role") not in self.roles:
            raise ValueError("实体类别或陈述角色未注册")
        class_id = entity.get("ontology_class_id")
        if class_id is not None:
            if not isinstance(class_id, str) or class_id not in self.domain_classes and class_id not in self.business_classes:
                raise ValueError("实体引用了未注册本体类别")
            upper = entity["class"]
            if upper != "Mention" and not self.domain_is_a(class_id, upper):
                raise ValueError("实体本体类别与物理或活动上层类别不符")

    def validate_relation(self, key: str, source_class: str, target_class: str,
                          *, role: str | None = None, statement_type: str | None = None) -> None:
        row = self.relations.get(key)
        if not row or not self.is_a(source_class, row["domain"]) or not self.is_a(target_class, row["range"]):
            raise ValueError("关系标识、方向或端点类别不符合本体注册表")
        if row.get("endpoint_pairs") and not any(self.is_a(source_class, a) and self.is_a(target_class, b) for a, b in row["endpoint_pairs"]):
            raise ValueError("关系跨越来源提及与真实对象，端点组合无效")
        if role is not None and role not in row.get("statement_roles", []): raise ValueError("陈述角色不允许建立该导航关系")
        if statement_type is not None and statement_type not in row.get("statement_types", []): raise ValueError("陈述类型与导航关系不匹配")

    def validate_semantic_relation(self, relation: dict, entities: list[dict],
                                   statements: dict | None = None, current_id: str | None = None) -> dict:
        row = self.relations.get(relation.get("relation_id"))
        if not row or not row.get("extractable", False): raise ValueError("未注册可抽取的语义关系")
        if 'source_ref' in relation or 'target_ref' in relation:
            if statements is None: raise ValueError('陈述关系必须在完整批次内校验')
            endpoints=[]
            for key in ('source_ref', 'target_ref'):
                reference=relation.get(key, {})
                owner=statements.get(reference.get('statement_id', current_id))
                if owner is None: raise ValueError('关系引用未知或未通过核验的陈述')
                if 'entity_index' in reference:
                    index=reference['entity_index']
                    if type(index) is not int or not 0 <= index < len(owner.get('entities', [])): raise ValueError('关系实体端点越界')
                    endpoint=owner['entities'][index]; self.validate_entity(endpoint)
                else: endpoint={'class':'Statement', 'statement':owner, 'statement_ref':reference.get('statement_id', current_id)}
                endpoints.append(endpoint)
            source,target=endpoints
            self.validate_relation(row['id'],source['class'],target['class'])
            for endpoint, field in ((source, 'source_statement_types'), (target, 'target_statement_types')):
                if (endpoint['class'] == 'Statement' and row.get(field)
                        and endpoint['statement']['type'] not in row[field]):
                    raise ValueError('关系端点陈述类型不匹配')
            if target['class']=='Statement' and row.get('statement_types') and target['statement']['type'] not in row['statement_types']:
                raise ValueError('关系目标陈述类型不匹配')
            if not relation.get('supports'): raise ValueError('陈述关系缺少独立精确支持成员')
            return {'relation_id':row['id'], 'source':source, 'target':target}
        indexes = [relation.get("source_entity"), relation.get("target_entity")]
        if any(type(index) is not int or index < 0 or index >= len(entities) for index in indexes):
            raise ValueError("语义关系端点未引用当前陈述实体")
        source, target = [entities[index] for index in indexes]
        self.validate_entity(source); self.validate_entity(target)
        self.validate_relation(row["id"], source["class"], target["class"])
        same_name = normalized(self.canonical_label(source["label"])) == normalized(self.canonical_label(target["label"])) and source["class"] == target["class"]
        distinct_occurrences = (self.is_a(source["class"], ["PhysicalObject", "Activity"])
                                and isinstance(source.get("occurrence"), dict) and isinstance(target.get("occurrence"), dict)
                                and source["occurrence"] != target["occurrence"])
        if same_name and not distinct_occurrences:
            raise ValueError("语义关系不允许自环")
        return {"relation_id": row["id"], "source": source, "target": target}

    def validate_skill(self, skill: dict) -> None:
        unknown = set(skill["relation_ids"]) - self.relations.keys()
        if unknown: raise ValueError("Skill 使用未注册关系：" + ",".join(sorted(unknown)))
        required = set(skill["required_items"])
        if required - set(self.statement_types):
            raise ValueError("Skill 使用未注册陈述类型")
        covered = {kind for key in skill["relation_ids"] for kind in self.relations[key].get("statement_types", [])}
        navigable = {kind for row in self.relations.values() if row.get("navigation")
                     for kind in row.get("statement_types", [])}
        # Kinds without graph navigation remain available through Evidence text.
        if not required & navigable <= covered: raise ValueError("Skill 必查项缺少本体导航关系")

    def extraction_schema(self) -> dict:
        """Validate the minimum source-backed statement and ontology references."""
        text = {"type": ["string", "null"]}
        occurrence = {"type": ["object", "null"], "properties": {key: text for key in ("identifier", "object", "location", "time")}}
        quantity = {"type": "object", "properties": {
            **{key: text for key in ("target", "parameter", "unit", "location")},
            "value": {"type": ["string", "number", "null"]},
            "operator": {"enum": ["eq", "ne", "lt", "le", "gt", "ge", "range", None]}}}
        formula = {"type": "object", "properties": {
            "expression": text,
            "variables": {"anyOf": [{"type":"null"}, {"type":"object"}, {"type":"array", "items":{"type":"object", "properties":{"symbol":text, "meaning":text}}}]},
            "units": {"type":["object", "null"], "additionalProperties":text},
            "conditions": {"type":["array", "null"], "items":{"type":["string", "object"]}}}}
        support={"type":"object", "required":["evidence_id","member_keys"], "properties":{
            "evidence_id":{"type":"string","minLength":1},"member_keys":{"type":"array","minItems":1,"items":{"type":"string","minLength":1}}}}
        endpoint={"type":"object", "properties":{"statement_id":{"type":"string","minLength":1},
            "entity_index":{"type":"integer","minimum":0}}, "required":["statement_id"], "additionalProperties":False}
        relation={"type":"object","required":["relation_id"],"properties":{
            "relation_id":{"enum":[key for key,row in self.relations.items() if row.get('extractable')]},
            "source_entity":{"type":"integer","minimum":0},"target_entity":{"type":"integer","minimum":0},
            "source_ref":endpoint,"target_ref":endpoint,"supports":{"type":"array","minItems":1,"items":support}},
            "oneOf":[{"required":["source_entity","target_entity"],"not":{"anyOf":[{"required":["source_ref"]},{"required":["target_ref"]}]}},
                     {"required":["source_ref","target_ref","supports"],"not":{"anyOf":[{"required":["source_entity"]},{"required":["target_entity"]}]}}],
            "additionalProperties":False}
        return {"type": "object", "properties": {
            "extraction_id":{"type":"string","minLength":1},
            "type": {"type": "string", "enum": self.statement_types},
            "text": {"type": "string", "minLength": 1, "pattern": "\\S"},
            "supports": {"type": "array", "minItems": 1, "items": support},
            "conditions": {"type": "array", "items": {"type": "object", "properties": {
                "text": {"type": "string", "minLength": 1},
                "expression": {"type": ["object", "string", "null"]}}}},
            "quantities": {"type":"array", "items":quantity},
            "formulas": {"type":"array", "items":formula},
            "entities": {"type": "array", "items": {"type": "object", "required": ["label", "class", "role"], "properties": {
                "label": {"type": "string", "minLength": 1},
                "occurrence": occurrence,
                "ontology_class_id": {"enum": [*self.business_classes, *self.domain_classes, None]},
                "class": {"type": "string", "enum": [key for key, row in self.classes.items() if row.get("extractable")]},
                "role": {"type": "string", "enum": list(self.roles)}}}},
            "semantic_relations": {"type": "array", "items": relation}
            }, "required": ["type", "text", "entities", "supports"]}

    def extraction_contract(self) -> dict:
        return {"ontology_version": self.version, "classes": [row for row in self.classes.values() if row.get("extractable")],
                "record_types": list(self.record_types.values()),
                "domain_classes": list(self.domain_classes.values()), "roles": list(self.roles.values()),
                "storage_policy":self.data.get('storage_policy',{}),
                "relations": list(self.relations.values()), "statement_schema": self.extraction_schema(),
                "completeness": {"quantity_required":["target", "target_binding", "parameter", "value", "unit", "operator", "location", "source_supports"],
                                 "formula_required":["expression", "variables", "units", "conditions", "source_supports"],
                                 "unknown":"保留null和missing_fields，completeness=incomplete，公式不得计算；真实实例身份绑定来源和明确对象/地点/时间，不使用陈述角色"},
                "rules": self.data["extraction_rules"], "navigation_policy": self.data["navigation_policy"]}

    @staticmethod
    def _class_choices(graph: Graph, node, names: list[str], ns, sh):
        choices, constraints = BNode(), []
        graph.add((node, sh["or"], choices))
        for name in names:
            item = BNode(); graph.add((item, sh["class"], ns[name])); constraints.append(item)
        Collection(graph, choices, constraints)

    def authority(self) -> tuple[Graph, Graph]:
        ns, sh = Namespace(self.data["namespace"]), Namespace("http://www.w3.org/ns/shacl#")
        core, shapes = Graph(), Graph()
        for graph in (core, shapes):
            graph.bind("tk", ns); graph.bind("owl", OWL); graph.bind("sh", sh); graph.bind("rdfs", RDFS); graph.bind("skos", SKOS)
        ontology = ns["ontology"]
        core.add((ontology, RDF.type, OWL.Ontology)); core.add((ontology, OWL.versionInfo, Literal(self.data["version"])))
        core.add((ontology, OWL.versionIRI, ns["version/" + self.version]))
        for key, row in self.business_classes.items():
            core.add((ns[key], RDF.type, OWL.Class)); core.add((ns[key], RDFS.subClassOf, OWL.Thing if row["parent"] == "Thing" else ns[row["parent"]]))
            core.add((ns[key], RDFS.label, Literal(row["zh"], lang="zh-CN")))
            self._describe(core, ns[key], row)
            for alias in row.get("aliases", []): core.add((ns[key], ns["alias"], Literal(alias, lang="zh-CN")))
            if row.get('storage_representation'): core.add((ns[key], ns['storageRepresentation'], Literal(row['storage_representation'])))
        for key, row in self.domain_classes.items():
            core.add((ns[key], RDF.type, OWL.Class))
            for parent in [row["parent"], *row.get("additional_parents", [])]:
                core.add((ns[key], RDFS.subClassOf, ns[parent]))
            core.add((ns[key], RDFS.label, Literal(row["label"], lang="zh-CN")))
            self._describe(core, ns[key], row)
            for alias in row.get("aliases", []):
                core.add((ns[key], ns["alias"], Literal(alias, lang="zh-CN")))
        roots = [ns[key] for key, row in self.business_classes.items() if row["parent"] == "Thing"]
        disjoint, head = BNode(), BNode(); core.add((disjoint, RDF.type, OWL.AllDisjointClasses)); core.add((disjoint, OWL.members, head))
        Collection(core, head, roots)
        for key, row in self.relations.items():
            core.add((ns[key], RDF.type, OWL.ObjectProperty)); core.add((ns[key], RDFS.label, Literal(row["zh"], lang="zh-CN")))
            self._describe(core, ns[key], row)
            if row.get('storage_representation'): core.add((ns[key], ns['storageRepresentation'], Literal(row['storage_representation'])))
            for predicate, names in ((RDFS.domain, row["domain"]), (RDFS.range, row["range"])):
                if len(names) == 1: endpoint = ns[names[0]]
                else:
                    endpoint, head = BNode(), BNode(); core.add((endpoint, RDF.type, OWL.Class)); core.add((endpoint, OWL.unionOf, head))
                    Collection(core, head, [ns[name] for name in names])
                core.add((ns[key], predicate, endpoint))
            shape, prop = ns[key + "Shape"], BNode()
            shapes.add((shape, RDF.type, sh.NodeShape)); shapes.add((shape, sh.targetSubjectsOf, ns[key])); shapes.add((shape, sh.nodeKind, sh.IRI))
            shapes.add((shape, sh.message, Literal("关系方向、端点类别及概念/实例组合必须符合注册表", lang="zh-CN")))
            self._class_choices(shapes, shape, row["domain"], ns, sh)
            shapes.add((shape, sh.property, prop)); shapes.add((prop, sh.path, ns[key])); shapes.add((prop, sh.nodeKind, sh.IRI))
            self._class_choices(shapes, prop, row["range"], ns, sh)
            if row.get("endpoint_pairs"):
                pair_shape, options, branches = BNode(), BNode(), []
                shapes.add((shape, sh.node, pair_shape)); shapes.add((pair_shape, sh["or"], options))
                for source, target in row["endpoint_pairs"]:
                    branch, constraint = BNode(), BNode(); shapes.add((branch, sh["class"], ns[source])); shapes.add((branch, sh.property, constraint))
                    shapes.add((constraint, sh.path, ns[key])); shapes.add((constraint, sh["class"], ns[target])); branches.append(branch)
                Collection(shapes, options, branches)
        for root in roots:
            shape = ns[str(root).split("#")[-1] + "CategoryShape"]
            shapes.add((shape, RDF.type, sh.NodeShape)); shapes.add((shape, sh.targetClass, root))
            constraint = BNode(); shapes.add((shape, sh["not"], constraint))
            self._class_choices(shapes, constraint, [str(other).split("#")[-1] for other in roots if other != root], ns, sh)
        # Storage records are documented separately from the business taxonomy.
        for key, row in self.record_types.items():
            shapes.add((ns[key], RDF.type, RDFS.Class))
            shapes.add((ns[key], RDFS.label, Literal(row["zh"], lang="zh-CN")))
            self._describe(shapes, ns[key], row)
        return core, shapes

    @staticmethod
    def _describe(graph, subject, row):
        graph.add((subject, SKOS.definition, Literal(row["definition"], lang="zh-CN")))
        if row.get("scope_note"):
            graph.add((subject, SKOS.scopeNote, Literal(row["scope_note"], lang="zh-CN")))
        for source in row.get("definition_sources", []):
            graph.add((subject, RDFS.seeAlso, URIRef(source)))

    def statement_projection(self, statements: list[dict]) -> Graph:
        """Project record types for relation validation; values remain JSON fields."""
        ns = Namespace(self.data["namespace"])
        graph = Graph()
        for statement in statements:
            graph.add((ns[statement["id"]], RDF.type, ns["Statement"]))
            for field in ("conditions", "quantities", "formulas"):
                values = statement.get(field, [])
                if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
                    raise ValueError("内嵌信息字段必须为对象数组")
        return graph

    def validate_graph(self, data: Graph) -> tuple[bool, Graph, str]:
        """No property inference can make an incorrectly typed edge pass."""
        from pyshacl import validate
        core, shapes = self.authority(); graph = Graph()
        for triple in data: graph.add(triple)
        for triple in core.triples((None, RDFS.subClassOf, None)): graph.add(triple)
        return validate(graph, shacl_graph=shapes, inference="none", advanced=False)
