"""Skill bodies are selected progressively and pinned by complete content hashes."""
from pathlib import Path
import pytest

from turbine_kg.agent import skills
from turbine_kg.ontology.registry import OntologyRegistry
from turbine_kg.runtime import fingerprint,PROJECT_ROOT


def test_discovery_uses_frontmatter_and_does_not_decode_instructions(monkeypatch):
    original=Path.read_text;decoded=[]
    def read(path,*args,**kwargs):
        if path.name=='SKILL.md':decoded.append(path.parent.name)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    catalog=skills.load_catalog(PROJECT_ROOT/'skills',registry=OntologyRegistry())
    assert {row['id'] for row in catalog}=={'requirements'}
    assert not decoded and not any('instructions' in row for row in catalog)
    selected=catalog.load_selected(['requirements','requirements'])
    assert len(selected)==1 and selected[0]['instructions']
    assert decoded==['requirements']
    catalog.load_selected(['requirements'])
    assert decoded==['requirements']


def test_selected_body_and_descriptors_are_not_shared_mutable_state():
    catalog=skills.load_catalog(PROJECT_ROOT/'skills',)
    descriptor=catalog[0];descriptor['description']='changed'
    assert catalog[0]['description']!='changed'
    selected=catalog.load_selected(['requirements']);selected[0]['instructions']='changed'
    assert catalog.load_selected(['requirements'])[0]['instructions']!='changed'
    with pytest.raises(ValueError,match='未知Skill'):catalog.load_selected(['missing'])


def test_full_skill_hash_changes_snapshot_and_rejects_mid_answer_edits(monkeypatch):
    catalog=skills.load_catalog(PROJECT_ROOT/'skills',)
    before=catalog.versions()
    original=skills.file_hash
    monkeypatch.setattr(skills,'file_hash',lambda path:fingerprint([original(path),'body edit']))
    after=skills.load_catalog(PROJECT_ROOT/'skills',).versions()
    assert before!=after and all('SKILL.md' not in value for value in after)
    with pytest.raises(ValueError,match='固定版本后改变'):catalog.load_selected(['requirements'])


def test_registry_navigation_contract_is_validated_before_skill_selection():
    class Registry:
        def validate_skill(self,descriptor):
            assert 'instructions' not in descriptor
            raise ValueError('unregistered relation')
    with pytest.raises(ValueError,match='unregistered relation'):skills.load_catalog(PROJECT_ROOT/'skills',registry=Registry())


def test_each_task_selects_only_skills_covering_its_actual_knowledge_item():
    from turbine_kg.agent.runner import plan_tasks,Session
    catalog=skills.load_catalog(PROJECT_ROOT/'skills',registry=OntologyRegistry())
    plan={'objects':['设备'],'object_tasks':[{'object':'设备','subquestion':'查询要求及程序','skill_ids':['requirements'],'knowledge_items':['requirement','procedure']}]}
    tasks,loaded,_=plan_tasks(plan,Session(),OntologyRegistry(),catalog,question='查询要求及程序')
    assert {task['knowledge_item']:task['skill_ids'] for task in tasks}=={'requirement':['requirements'],'procedure':['requirements']}
    assert len(tasks)==2 and not any(task['requires_site_inputs'] or task['calculation_required'] for task in tasks)
    required=next(task for task in tasks if task['knowledge_item']=='requirement')
    projected=skills.task_skill_context(loaded,[required])
    assert [row['id'] for row in projected]==['requirements']
    assert projected[0]['instructions']==catalog.load_selected(['requirements'])[0]['instructions']
    assert projected[0]['task_ids']==[required['id']] and projected[0]['knowledge_items']==['requirement']
    assert skills.task_skill_context(loaded,[{**required,'knowledge_item':'procedure'}])[0]['knowledge_items']==['procedure']
    assert not skills.task_skill_context(loaded,[{**required,'knowledge_item':'unregistered'}])
    assert all(row['version'] and row['content_hash'] for row in catalog)
