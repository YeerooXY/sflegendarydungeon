"""Build native evidence for a per-video HUD obstruction audit."""
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from sfld_vod.batch import select_clips
from sfld_vod.media import sha256,write_json
from sfld_vod.vision import rect
from vod_room_library import LIBRARY_ROOTS,ROOT

OUT=ROOT/'out/vod-room-library-audit'


def prepare(job):
    import cv2
    source_path,layout_path=map(Path,job)
    root=source_path.parent.parent;source=json.loads(source_path.read_text());layout=json.loads(layout_path.read_text())
    view_id=layout_path.stem;clips=select_clips(source_path.parent/'720p') or select_clips(source_path.parent/'360p')
    if not clips:return None
    stamps=[]
    queue=source_path.parent/'pair-evidence-v1.3.0/queue.json'
    if queue.exists():stamps=[r['best_time'] for r in json.loads(queue.read_text())['rows']]
    else:
        for p in source_path.parent.rglob('scenes.json'):
            if source['id']!=view_id and view_id not in p.parts:continue
            d=json.loads(p.read_text());stamps.extend(e['best_time'] for e in d.get('epochs',[]))
    stamps=sorted(set(stamps))
    planned=([stamps[int((len(stamps)-1)*q)] for q in (.1,.5,.9)] if stamps else
             [c['source_start']+(c['source_end']-c['source_start'])/2 for _,c in (clips[0],clips[len(clips)//2],clips[-1])])
    if layout.get('calibration_source_seconds') is not None:planned[0]=layout['calibration_source_seconds']
    folder=OUT/view_id;folder.mkdir(parents=True,exist_ok=True)
    proofs=[]
    for i,stamp in enumerate(planned):
        matches=[(m,c) for m,c in clips if c['source_start']<=stamp<c['source_end']]
        if not matches:
            m,c=min(clips,key=lambda mc:min(abs(stamp-mc[1]['source_start']),abs(stamp-mc[1]['source_end'])))
            stamp=max(c['source_start'],min(stamp,c['source_end']-.5));matches=[(m,c)]
        m,c=matches[0];cap=cv2.VideoCapture(str(m.parent/c['file']))
        cap.set(cv2.CAP_PROP_POS_MSEC,(stamp-c['source_start'])*1000);ok,frame=cap.read();cap.release()
        if not ok:continue
        x,y,w,h=rect(layout['crop'],frame.shape[1],frame.shape[0]);game=frame[y:y+h,x:x+w]
        full=folder/f'{i}-game.jpg';hud=folder/f'{i}-hud.png'
        cv2.imwrite(str(full),game,[cv2.IMWRITE_JPEG_QUALITY,97])
        cv2.imwrite(str(hud),game[round(h*.78):,:round(w*.22)])
        proofs.append({'source_seconds':stamp,'game':full.relative_to(OUT).as_posix(),'hud':hud.relative_to(OUT).as_posix(),
                       'game_sha256':sha256(full),'hud_sha256':sha256(hud),'video_sha256':c['sha256'],
                       'video_path':str((m.parent/c['file']).relative_to(ROOT)).replace('\\','/'),'clip_start':c['source_start']})
    return {'source_id':source['id'],'view_id':view_id,'uploader':source.get('uploader'),'url':source['url'],
            'upload_date':source.get('upload_date'),'library_root':str(root.relative_to(ROOT)).replace('\\','/'),
            'samples':proofs,'obstruction_review':None}


def main():
    from PIL import Image,ImageDraw,ImageFont
    jobs=[]
    for root in LIBRARY_ROOTS:
        for source in sorted((ROOT/root).glob('*/source.json')):
            layouts=[source.parent.parent/'layouts'/f'{source.parent.name}.json']
            if not layouts[0].is_file():layouts=sorted((source.parent.parent/'layouts').glob(source.parent.name+'-*.json'))
            jobs.extend((str(source),str(layout)) for layout in layouts)
    OUT.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=6) as pool:rows=[r for r in pool.map(prepare,jobs) if r]
    rows.sort(key=lambda r:(r['uploader'].lower(),r['source_id'],r['view_id']))
    write_json(OUT/'audit.json',{'schema':1,'rows':rows,'note':'Three native samples per view for manual obstruction review; not an accuracy or completeness census.'})
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',16)
    for page in range(0,len(rows),12):
        sheet=Image.new('RGB',(1440,960),(17,25,31));draw=ImageDraw.Draw(sheet)
        for i,row in enumerate(rows[page:page+12]):
            x=(i%3)*480;y=(i//3)*240
            draw.text((x+5,y+2),row['uploader']+' / '+row['view_id'],font=font,fill='white')
            draw.text((x+5,y+22),str(row['upload_date']),font=font,fill='#e7bf61')
            for col,sample in enumerate([row['samples'][0],row['samples'][-1]]):
                image=Image.open(OUT/sample['hud']).convert('RGB');image.thumbnail((235,174))
                sheet.paste(image,(x+col*240,y+45))
                draw.text((x+col*240+3,y+219),f"{sample['source_seconds']:.3f}s",font=font,fill='white')
        sheet.save(OUT/f'contact-{page//12+1:02}.jpg',quality=96)
    print(json.dumps({'videos':len({r['source_id'] for r in rows}),'views':len(rows),'samples':sum(len(r['samples']) for r in rows)},indent=2))


if __name__=='__main__':main()
