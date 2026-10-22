"""Create review aids from the newer VOD ledger; no predictions become observations."""
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT/'out/vod-hunt-2026-10-12'
OUT = ROOT/'out/opening-audit-2026-10-17'


def main():
    path = ROOT/'data/vods/reviewed-offers-2026-10-12.jsonl'
    all_rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows = [r for r in all_rows if r.get('status')=='decision' and r.get('room') and 1<=r['room']<=5]
    rows.sort(key=lambda r:(r['source_id'],r['start']))
    OUT.mkdir(parents=True,exist_ok=True)
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',19)
    for index,row in enumerate(rows):
        row['opening_audit_index']=index
        for evidence in row['evidence']:
            image=WORK/evidence['image']
            assert hashlib.sha256(image.read_bytes()).hexdigest()==evidence['sha256']
    (OUT/'opening-candidates.json').write_text(json.dumps(rows,indent=2)+'\n',encoding='utf-8')
    for offset in range(0,len(rows),6):
        canvas=Image.new('RGB',(1200,1500),(19,25,31));draw=ImageDraw.Draw(canvas)
        for number,row in enumerate(rows[offset:offset+6]):
            x=number%2*600;y=number//2*500
            image=Image.open(WORK/row['evidence'][0]['image']).convert('RGB')
            w,h=image.size
            draw.text((x+8,y+4),f"#{offset+number} {row['source_id']}  {row['start']:.3f}s  R{row['room']}",font=font,fill='white')
            thumb=image.copy();thumb.thumbnail((580,400))
            canvas.paste(thumb,(x+10,y+30))
            hud=image.crop((0,int(h*.91),int(w*.20),h));hud.thumbnail((275,58))
            canvas.paste(hud,(x+8,y+435))
            draw.text((x+295,y+445),'Native counter crop',font=font,fill='#d3b866')
        canvas.save(OUT/f'offers-{offset:03}.jpg',quality=97)
    print(json.dumps(dict(retained=len(rows),sheets=(len(rows)+5)//6,source_sha256=hashlib.sha256(path.read_bytes()).hexdigest())))


if __name__=='__main__':main()
