"""Use the visible top-left counter copy in four Tuny recordings."""
from pathlib import Path
from vod_room_sequence import inventory_jobs,execute_jobs
from vod_room_library import ROOT,LIBRARY_ROOTS

BOXES={
    '2881023333':[.008,.020,.115,.052],
    '2881884794':[.008,.020,.115,.052],
    '2882738740':[0,0,.115,.052],
    '2883589492':[.008,.020,.115,.052],
}


def main():
    jobs=[]
    for root in LIBRARY_ROOTS:
        for job in inventory_jobs(ROOT/root,sources=list(BOXES)):
            job.update(counter_location='top',counter_box=BOXES[job['source_id']]);jobs.append(job)
    execute_jobs(jobs,2)


if __name__=='__main__':main()
