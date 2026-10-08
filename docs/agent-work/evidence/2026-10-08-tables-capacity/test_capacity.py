"""Isolated capacity acceptance: real Store/capabilities, synthetic records, no network."""
import json, os, platform, resource, subprocess, time
from pathlib import Path
from apps.suite.tests.conftest import suite, _no_worker_outlives_its_test
from apps.suite.tests.test_tables import table, field, out, query, denied


def test_capacity(suite):
    s = suite
    tid = table(s, 'Synthetic capacity acceptance 20261008')
    number = field(s, tid, 'Sequence', 'number', is_unique=True)
    computed = field(s, tid, 'Double', 'formula', config={'expression': '{Sequence} * 2', 'decimals': 2})
    batches=[]
    started=time.perf_counter()
    for start in range(0,10000,100):
        t=time.perf_counter()
        result=out(s,'record.batch',{'table_id':tid,'atomic':True,'items':[{'action':'create','cells':{number:str(n)}} for n in range(start,start+100)]})
        assert result['succeeded']==100 and result['failed']==0
        batches.append(time.perf_counter()-t)
        if start % 2000 == 0: print('inserted',start+100,flush=True)
    insert=time.perf_counter()-started
    timings=[]
    for _ in range(10):
        t=time.perf_counter()
        result=query(s,tid,filters=[{'field_id':number,'op':'gte','value':'5000'}],sort=[{'field_id':number,'direction':'desc'}],limit=50)
        timings.append(time.perf_counter()-t)
        assert result['total']==5000 and len(result['records'])==50
        assert result['records'][0]['cells'][number]=='9999'
        assert result['records'][0]['cells'][computed]=='19998.00'
    seen=set(); offset=0; pages=0
    t=time.perf_counter()
    while True:
        page=query(s,tid,sort=[{'field_id':number}],limit=200,offset=offset)
        assert page['total']==10000
        ids={r['id'] for r in page['records']}
        assert len(ids)==len(page['records']) and not seen.intersection(ids)
        seen.update(ids); pages+=1
        if page['next_offset'] is None: break
        assert page['next_offset']>offset
        offset=page['next_offset']
    pagination=time.perf_counter()-t
    assert len(seen)==10000 and pages==50
    denied('record_limit',lambda:out(s,'record.create',{'table_id':tid,'cells':{number:'10000'}}),status=409)
    first=query(s,tid,sort=[{'field_id':number}],limit=1)['records'][0]
    partial=out(s,'record.batch',{'table_id':tid,'atomic':False,'items':[
      {'action':'create','cells':{number:'10000'}},
      {'action':'update','record_id':first['id'],'rev':first['rev'],'cells':{number:'-1'}}]})
    assert partial['failed']==1 and partial['succeeded']==1
    assert partial['items'][0]['error']=='record_limit'
    assert query(s,tid,limit=1)['total']==10000
    report={
      'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
      'platform':platform.system(), 'architecture':platform.machine(),'python':platform.python_version(),
      'logical_cpus':os.cpu_count(),'rows':10000,'fields':2,'concurrency':1,
      'workload':'Synthetic unique number + dependent formula; real capability transactions, no HTTP/network',
      'insert_100x100_s':insert,'batch_max_s':max(batches),
      'filter_sort_first_50_s':timings,'filter_sort_p95_s':sorted(timings)[-1],
      'paginate_50x200_s':pagination,'unique_record_ids':len(seen),
      'over_limit_error':'record_limit','partial_batch':{'succeeded':partial['succeeded'],'failed':partial['failed']},
      'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
      'budgets':{'insert_s':120,'batch_max_s':10,'filter_sort_p95_s':5,'pagination_s':30},
      'limitations':'Local Apple arm64 single worker; excludes HTTP/browser and production load; no extrapolation to concurrency.'}
    Path('/private/tmp/synwevo-tables-capacity-20261008.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)
    assert insert<120 and max(batches)<10 and max(timings)<5 and pagination<30
