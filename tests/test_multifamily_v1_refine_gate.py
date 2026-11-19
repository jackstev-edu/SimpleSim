"""Refinement orchestration tests. Fake solver values are not FEA evidence."""
import json
from pathlib import Path
import pytest
from src.data.multifamily_v1 import fea_gate as g, refine_gate as r
from src.data.multifamily_v1.templates import baseline, FAMILIES
from tests.test_multifamily_v1_fea_gate import frozen_inputs, fake_result

REVIEW={'T01','T02','T04','T07','T08','T09','T10'}

@pytest.mark.parametrize('key',list(FAMILIES))
def test_extra_mesh(key):
    c=baseline(key); f=g.mesh_parameters(c,'fine'); x=r.extra_mesh(c)
    assert 0<x['minimum_size_mm']<f['minimum_size_mm']
    assert x['maximum_size_mm']==pytest.approx(f['maximum_size_mm']*2/3)
    assert x['curvature_elements']==36 and x['algorithm_3d']==10 and x['high_order_optimize']==2


def test_refine_only_review_and_resume(frozen_inputs,tmp_path):
    calls=[]
    def solver(c,folder,mesh,timeout):
        n=mesh['curvature_elements']; calls.append((c.template_id,n)); v=fake_result(c,folder,mesh,timeout)
        v.max_stress_mpa=100 if n==16 else (105 if c.template_id in REVIEW else 101)
        if n==36: v.max_stress_mpa=106; v.node_count=300; v.volume_element_count=150
        v.factor_of_safety=250/v.max_stress_mpa
        return v
    a=g.generate(*frozen_inputs,tmp_path,['medium','fine'],10,solver); assert a['convergence_pass']==3
    path=tmp_path/'cases/T01/fine/result.json'; before=path.read_bytes(); calls.clear()
    b=r.generate(*frozen_inputs,tmp_path,10,solver)
    assert set(calls)=={(k,36) for k in REVIEW} and len(calls)==7
    assert b['baseline_gate_pass'] and b['additional_successful_solves']==7
    assert {k for k,v in b['provisional_production_levels'].items() if v=='fine'}==REVIEW
    assert b['formal_labels_added']==0 and path.read_bytes()==before
    calls.clear(); assert r.generate(*frozen_inputs,tmp_path,10,solver)==b and not calls


def test_refine_still_review_and_failure(frozen_inputs,tmp_path):
    def solver(c,folder,mesh,timeout):
        n=mesh['curvature_elements']; v=fake_result(c,folder,mesh,timeout)
        if n==36 and c.template_id=='T02':raise RuntimeError('injected error')
        v.max_stress_mpa=100 if n==16 else (105 if n==24 else 112)
        if n==36:v.node_count=300;v.volume_element_count=150
        v.factor_of_safety=250/v.max_stress_mpa
        return v
    g.generate(*frozen_inputs,tmp_path,['medium','fine'],10,solver)
    b=r.generate(*frozen_inputs,tmp_path,10,solver)
    assert not b['baseline_gate_pass'] and len(b['convergence_review'])==10
    assert b['failed'][0]['template_id']=='T02' and b['formal_labels_added']==0
