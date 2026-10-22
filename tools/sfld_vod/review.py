"""Review pages and conservative frequency summaries for video observations."""
from __future__ import annotations

from collections import Counter, defaultdict
import html
import json
import math
from pathlib import Path
from urllib.parse import urlparse

from .media import sha256, write_json

DOORS = ("monster","mystery","golden","wall","locked","double_locked","unlocked","epic",
         "cursed","sacrifice","hungry","blessing","destiny","shop","boss","trial","exit_trial")
PHYSICAL = set(DOORS) - {"monster","mystery","golden","shop","boss","trial","exit_trial"}


def wilson(successes, total):
    if not isinstance(total,int) or not isinstance(successes,int) or not 0 <= successes <= total or total == 0:
        raise ValueError("Wilson interval needs integer counts and a nonzero denominator.")
    z=1.959963984540054; p=successes/total; denominator=1+z*z/total
    center=(p+z*z/(2*total))/denominator
    half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/denominator
    return {"successes":successes,"opportunities":total,"estimate":p,
            "wilson_95":[max(0.,center-half),min(1.,center+half)]}


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def reviewed_counts(candidates, reviews):
    originals={c["id"]:c for c in candidates}
    if len(originals)!=len(candidates):
        raise ValueError("Duplicate candidate IDs; do not combine overlapping scans as independent data.")
    seen=set(); decision_keys=set(); groups=defaultdict(list); coverage=Counter(total_candidates=len(candidates))
    for row in reviews:
        ident=row.get("id")
        if ident not in originals or ident in seen:
            raise ValueError("Unknown or duplicate reviewed candidate ID.")
        seen.add(ident); candidate=originals[ident]
        if row.get("image_sha256")!=candidate["image_sha256"]:
            raise ValueError("Review does not match the candidate image hash.")
        status=row.get("status")
        if status not in {"decision","duplicate","not_decision","uncertain"}:
            raise ValueError("Invalid review status.")
        coverage[status]+=1
        if status!="decision":
            continue
        if row.get("generation") not in {"ordinary","scripted","unknown"}:
            raise ValueError("Classify ordinary versus scripted decisions explicitly.")
        doors=row.get("doors")
        if not isinstance(doors,list) or len(doors)!=2 or any(d.get("kind") not in DOORS for d in doors):
            raise ValueError("A reviewed decision needs two identified choices.")
        if any(d.get("trap") is not None and type(d.get("trap")) is not bool for d in doors):
            raise ValueError("Trap presence must be true, false, or unknown.")
        ctx=row.get("context",{})
        if not isinstance(ctx,dict):
            raise ValueError("Expected reviewed context.")
        stage=ctx.get("stage")
        if stage is not None and (type(stage) is not int or not 1<=stage<=4):
            raise ValueError("Stage must be 1-4 or unknown.")
        room=ctx.get('room'); run_id=ctx.get('run_id')
        if room is not None and (type(room) is not int or not 1<=room<=100):
            raise ValueError('Room must be 1-100 or unknown.')
        if room is not None:
            inferred=(room-1)//25+1
            if stage is not None and stage!=inferred:
                raise ValueError('Room and stage disagree.')
            stage=inferred
        if run_id is not None and (not isinstance(run_id,str) or not run_id.strip()):
            raise ValueError('Run label must be a nonempty string or unknown.')
        if room is not None and run_id is not None:
            key=(candidate['source_id'],run_id,room)
            if key in decision_keys:
                raise ValueError('Repeated source/run/room: mark repeated views as duplicates.')
            decision_keys.add(key)
        if ctx.get("run_class") not in (None,"first","later"):
            raise ValueError("Event-run class must be first, later, or unknown.")
        gems=ctx.get("gems")
        if gems is not None and (not isinstance(gems,list) or any(not isinstance(g,str) for g in gems)):
            raise ValueError("Stones must be an explicit list or unknown.")
        if row["generation"]!="ordinary":
            continue
        # Source stays in the grouping: older uploads are never silently pooled.
        group={"source_id":candidate["source_id"],"stage":stage,"run_class":ctx.get("run_class"),
               "gems":None if gems is None else sorted(set(gems)),"mode":ctx.get("mode"),"theme":ctx.get("theme")}
        groups[json.dumps(group,sort_keys=True)].append(row)
    coverage["unreviewed"]=len(candidates)-len(seen)
    output=[]
    for key,rows in groups.items():
        pairs=Counter(tuple(sorted(d["kind"] for d in r["doors"])) for r in rows)
        presence=Counter()
        physical=Counter()
        trap_known=trap_yes=0
        for row in rows:
            presence.update(set(d["kind"] for d in row["doors"]))
            physical.update(d["kind"] for d in row["doors"] if d["kind"] in PHYSICAL)
            traps=[d.get("trap") for d in row["doors"]]
            if True in traps:
                trap_known+=1;trap_yes+=1
            elif all(v is False for v in traps):
                trap_known+=1
        output.append({"context":json.loads(key),"decisions":len(rows),
                       "pairs":[{"choices":list(pair),**wilson(n,len(rows))} for pair,n in sorted(pairs.items())],
                       "presence_in_pair":{k:wilson(v,len(rows)) for k,v in sorted(presence.items())},
                       "physical_door_counts":dict(sorted(physical.items())),
                       "physical_door_frequencies":{k:wilson(v,sum(physical.values())) for k,v in sorted(physical.items())},
                       "physical_door_opportunities":sum(physical.values()),
                       "pair_has_trap":wilson(trap_yes,trap_known) if trap_known else None,
                       "trap_status_unknown":len(rows)-trap_known})
    return {"schema":1,"coverage":dict(coverage),"groups":output,"calibrated":False,
            "interpretation":["Only explicitly reviewed ordinary decisions are counted.",
                              "Each pair is one observation; physical-door subtype counts use their own denominator.",
                              "Missing categories are unobserved, not proof of zero probability.",
                              "Wilson intervals assume independent comparable decisions. They exclude detector errors, missed screens and run clustering.",
                              "This selected reviewed subset may be biased. Review rejected spans and whole held-out runs before calibrating the simulator."]}


def make_review(directory):
    directory=Path(directory).resolve(); data=read_jsonl(directory/"candidates.jsonl")
    # Images are local relative paths generated by the scanner, never remote markup.
    for row in data:
        image=(directory/row["image"]).resolve()
        if not image.is_relative_to(directory) or sha256(image)!=row["image_sha256"]:
            raise ValueError("Candidate image is missing, changed, or outside this scan.")
        url=urlparse(row.get('source_url') or '')
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password:
            raise ValueError('Review requires a public HTTP(S) source URL without credentials.')
    payload=json.dumps(data,ensure_ascii=True).replace("<","\\u003c")
    options="<option value=''>Unknown</option>"+"".join(f"<option>{html.escape(d)}</option>" for d in DOORS)
    document=r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dungeon VOD review</title><style>
body{font:16px system-ui;background:#101c22;color:#e8e7df;max-width:1150px;margin:30px auto;padding:0 20px}button,select,input{font:inherit;background:#22353d;color:inherit;padding:9px;border:1px solid #577078;border-radius:5px}button{cursor:pointer}main{display:grid;grid-template-columns:2fr 1fr;gap:24px}img{width:100%;image-rendering:auto;border:1px solid #577078}label{display:block;margin:10px 0}a{color:#edc676}small,p{color:#bbc7ca}.tools{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}#status{color:#edc676}details{margin:16px 0}textarea{width:100%}@media(max-width:700px){main{display:block}}
</style><h1>Dungeon VOD review</h1><p>These are visual candidates, not measured odds. Check both choices and the room context. Mark repeated views as duplicates. Unknown trap presence does not mean no trap.</p>
<div class="tools"><button id="previous">Previous</button><button id="next">Next</button><span id="position"></span><button id="export">Export reviews</button></div>
<main><section><img id="frame" alt="Candidate dungeon frame"><p><a id="source" target="_blank" rel="noopener">Open source near this frame</a></p><p id="scores"></p><details><summary>Source record</summary><pre id="record" style="white-space:pre-wrap"></pre></details></section>
<section><label>Review status<select id="verdict"><option value="">Unreviewed</option><option value="decision">Decision screen</option><option value="duplicate">Duplicate view</option><option value="not_decision">Not a decision</option><option value="uncertain">Uncertain</option></select></label>
<label>Left choice<select id="left">__OPTIONS__</select></label><label>Left trap<select id="lefttrap"><option value="">Unknown</option><option value="true">Present</option><option value="false">Absent</option></select></label>
<label>Right choice<select id="right">__OPTIONS__</select></label><label>Right trap<select id="righttrap"><option value="">Unknown</option><option value="true">Present</option><option value="false">Absent</option></select></label>
<label>Generation<select id="generation"><option value="unknown">Unknown</option><option value="ordinary">Ordinary</option><option value="scripted">Scripted / boss / compulsory shop</option></select></label>
<label>Stage<select id="stage"><option value="">Unknown</option><option>1</option><option>2</option><option>3</option><option>4</option></select></label>
<label>Room number<input id="room" type="number" min="1" max="100" placeholder="Unknown"></label><label>Run label within this source<input id="runid" placeholder="Unknown; e.g. run-2"></label>
<label>Run within event<select id="run"><option value="">Unknown</option><option value="first">First run</option><option value="later">Later run</option></select></label>
<label>Held stones<input id="gems" placeholder="Unknown; enter none for no stones"></label><small>Use simulator stone IDs, separated by commas. Never infer a first run from the first scene in a VOD.</small>
<div class="tools"><button id="save">Save review</button></div><p id="status"></p></section></main>
<script>const rows=__PAYLOAD__;const $=id=>document.getElementById(id);let index=0;
const storageKey='sfld-vod-review:__DIGEST__';let reviews={};try{reviews=JSON.parse(localStorage.getItem(storageKey)||'{}')}catch(e){}
function render(){if(!rows.length){$('position').textContent='No candidates in this section.';$('save').disabled=true;return}const r=rows[index],saved=reviews[r.id];$('position').textContent=`${index+1} / ${rows.length} candidates`; $('frame').src=r.image; $('record').textContent=JSON.stringify(r,null,2);$('scores').textContent='Template scores are similarity scores, not probabilities. '+r.suggested_doors.map(d=>(d.kind||'unknown')+' '+d.score).join(' / ');
let u=new URL(r.source_url||'https://www.twitch.tv');if(u.hostname.endsWith('youtube.com'))u.searchParams.set('t',Math.floor(r.source_seconds)+'s');else u.searchParams.set('t',Math.floor(r.source_seconds/3600)+'h'+Math.floor(r.source_seconds%3600/60)+'m'+Math.floor(r.source_seconds%60)+'s');$('source').href=u.href;
$('verdict').value=saved?.status||'';$('generation').value=saved?.generation||'unknown';for(const [side,i] of [['left',0],['right',1]]){$(side).value=saved?.doors?.[i]?.kind||r.suggested_doors[i].kind||'';const trap=saved?.doors?.[i]?.trap;$(side+'trap').value=trap==null?'':String(trap)}$('stage').value=saved?.context?.stage||'';$('room').value=saved?.context?.room||'';$('runid').value=saved?.context?.run_id||'';$('run').value=saved?.context?.run_class||'';$('gems').value=saved?.context?.gems==null?'':saved.context.gems.length?saved.context.gems.join(','):'none';$('status').textContent=saved?'Saved locally. Export reviews to keep a file.':'';}
$('previous').onclick=()=>{index=Math.max(0,index-1);render()};$('next').onclick=()=>{index=Math.min(rows.length-1,index+1);render()};
$('save').onclick=()=>{const r=rows[index],status=$('verdict').value;if(!status){$('status').textContent='Choose a review status.';return}if(status==='decision'&&(!$('left').value||!$('right').value)){$('status').textContent='Identify both choices or mark this uncertain.';return}const room=$('room').value?Number($('room').value):null,stage=$('stage').value?Number($('stage').value):null;if(room!==null&&(!Number.isInteger(room)||room<1||room>100||stage!==null&&stage!==Math.floor((room-1)/25)+1)){$('status').textContent='Room must be 1-100 and agree with the stage.';return}const gs=$('gems').value.trim();reviews[r.id]={id:r.id,image_sha256:r.image_sha256,status,generation:$('generation').value,doors:['left','right'].map(s=>({kind:$(s).value||null,trap:$(s+'trap').value===''?null:$(s+'trap').value==='true'})),context:{stage,room,run_id:$('runid').value.trim()||null,run_class:$('run').value||null,gems:gs===''?null:gs==='none'?[]:gs.split(',').map(s=>s.trim()).filter(Boolean)},reviewed_at:new Date().toISOString()};try{localStorage.setItem(storageKey,JSON.stringify(reviews));$('status').textContent='Saved locally. Export reviews to keep a file.'}catch(e){$('status').textContent='Saved in this tab only; export now because browser storage is unavailable.'}};
$('export').onclick=()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([Object.values(reviews).map(r=>JSON.stringify(r)).join('\n')+'\n'],{type:'application/x-ndjson'}));a.download='reviews.jsonl';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)};render();</script></html>'''
    (directory/"review.html").write_text(document.replace("__OPTIONS__",options).replace("__DIGEST__",sha256(directory/'candidates.jsonl')).replace("__PAYLOAD__",payload),encoding="utf-8")
    return directory/"review.html"
