#!/usr/bin/env python3
"""Offline firmware evidence probe, not a camera capture tool. Requires Unicorn.

Run .venv/bin/python frame_access_probe.py from the repository root.
It needs your own extracted MAIN image at analysis/MAIN_c0000000.bin; no
firmware bytes are published here.
No transport imports, camera commands, MMIO access or firmware writes on hardware.
"""

import json, struct, hashlib
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm_const import *
fw=(Path(__file__).resolve().parent.parent/'analysis'/'MAIN_c0000000.bin').read_bytes()
assert hashlib.sha256(fw).hexdigest()=='92a8ee993f6c3d66c251e88d45a2ccd5135c6cf7342717784321c2ed506e2fb4'
u=Uc(UC_ARCH_ARM,UC_MODE_ARM)
u.mem_map(0xC0000000,0x4000000); u.mem_write(0xC0000000,fw)
u.mem_map(0x100000,0x10000)
OBJ=0xC351FE88; SERVICE=0x102000; OBS=0x103000; EVENT=0x104000; RET=0x100000; STACK=0x10F000
R=[UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3]
def put(a,v): u.mem_write(a,struct.pack('<I',v))
def get(a): return struct.unpack('<I',u.mem_read(a,4))[0]
events=[]
reads=[]
trigger=False
observer=0
def boundary(uc,a,size,data):
    global observer
    args=[uc.reg_read(r) for r in R]
    value=0
    if a==0xC03A0750: value=SERVICE
    elif a==0xC0015058: uc.mem_write(args[0],bytes([args[1]&255])*args[2])
    elif a==0xC03A0798:
        assert args[0]==SERVICE
        events.append({'message':get(args[1]),'flag':uc.mem_read(args[1]+4,1)[0],'argument':get(args[1]+0x10)})
        value=0xA5
        if trigger and get(args[1])==27:
            put(EVENT,2); put(EVENT+12,0); put(EVENT+16,0); put(EVENT+20,0x106000)
            uc.reg_write(UC_ARM_REG_R0,observer); uc.reg_write(UC_ARM_REG_R1,EVENT)
            uc.reg_write(UC_ARM_REG_PC,0xC0407318)
            return
    elif a==0xC03A0C68:
        observer=args[1]
        events.append({'register':hex(observer)})
    elif a==0xC03A0D20:
        events.append({'unregister':hex(args[1])})
    elif a==0xC03705D8: events.append({'sleep_argument':args[0]})
    elif a in (0x105100,0xC0023448): events.append({'output_call':hex(a)})
    elif a==0xC0013090: pass # descriptor's lazy initialization already completed
    elif a==0xC001510C:
        uc.mem_write(args[0],bytes(uc.mem_read(args[1],args[2]))); value=args[0]
    elif a==0xC01822B8:
        reads.append(args[0]); value=1 # synthetic hardware ring index
    else: return
    uc.reg_write(UC_ARM_REG_R0,value)
    uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))
u.hook_add(UC_HOOK_CODE,boundary)
def run(a,*args):
    for r,v in zip(R,args): u.reg_write(r,v)
    u.reg_write(UC_ARM_REG_SP,STACK); u.reg_write(UC_ARM_REG_LR,RET)
    u.emu_start(a,RET,count=100000)
    assert u.reg_read(UC_ARM_REG_PC)==RET and u.reg_read(UC_ARM_REG_SP)==STACK
    return u.reg_read(UC_ARM_REG_R0)
run(0xC03716F0,OBJ)
assert get(OBJ)==0xC0B9928C and get(OBJ+4)==SERVICE
ctor={'object':hex(OBJ),'vtable':hex(get(OBJ)),'service_stub':hex(get(OBJ+4))}
assert run(0xC03714A8,OBJ,1)==0xA5
assert run(0xC03714E0,OBJ)==0xA5
messages=events[:]; events.clear()
assert messages==[{'message':26,'flag':1,'argument':1},{'message':27,'flag':1,'argument':0}]
run(0xC0406E78,OBJ,0x105000)
assert events[0].keys()=={'register'}
assert events[1:3]==messages
assert events[3:23]==[{'sleep_argument':50}]*20
assert events[23]=={'message':26,'flag':1,'argument':0}
assert events[24]=={'unregister':events[0]['register']}
assert len(events)==25
callback=[]
for kind,status in [(2,0),(2,1),(2,2),(2,3),(1,0),(3,0)]:
    u.mem_write(OBS,bytes(16)); u.mem_write(EVENT,bytes(24))
    put(EVENT,kind); put(EVENT+12,status); put(EVENT+16,0x1234); put(EVENT+20,0x105000)
    assert run(0xC0407318,OBS,EVENT)==1
    accepted=kind==2 and status<=2
    assert bool(u.mem_read(OBS+4,1)[0])==accepted
    assert get(OBS+8)==(0x1234 if accepted else 0)
    assert get(OBS+12)==(0x105000 if accepted else 0)
    callback.append({'event_kind':kind,'status':status,'completion_written':accepted})
result={'constructor':ctor,'messages':messages,'timeout_lifecycle':events[:],'observer_cases':callback}

events.clear(); trigger=True; put(0x105000,0x105100)
run(0xC0406E78,OBJ,0x105000)
assert not any('sleep_argument' in event for event in events)
assert events[-2]=={'message':26,'flag':1,'argument':0}
assert events[-1]=={'unregister':events[0]['register']}
assert sum('output_call' in event for event in events)==4
result['success_lifecycle']=events[:]

reports=[]
for state,channel in [(3,0),(3,1),(3,2),(3,None),(0,0),(1,0),(2,0),(5,0)]:
    reads.clear(); put(SERVICE+12,state); put(SERVICE+0x54,0x1234)
    for i in range(3):
        record=0xC30255B4+i*0x80
        u.mem_write(record,bytes(0x80)); put(record,0 if i==channel else 2)
        put(record+12,640+i*16); put(record+16,360+i*8)
        put(record+0x28,0x01800000+i*0x100000) # slot1, word-addressed
        put(record+0x7C,1)
    desc=run(0xC0429560,SERVICE)
    expected=state not in (0,1,5) and channel is not None
    assert desc==0xC375C094
    width,height=get(desc),get(desc+4)
    address=get(desc+0x1C)
    assert width==(640+channel*16 if expected else 0)
    assert height==(360+channel*8 if expected else 0)
    assert address==(0x46000000+channel*0x400000 if expected else 0)
    assert get(desc+0x2C)==(0x1234 if expected and channel==0 else 0)
    reports.append({'state':state,'first_kind_zero_channel':channel,'width':width,'height':height,'pointer':hex(address),'hardware_index_reads':len(reads)})

result['driver_cases']=reports
result['image_channels']=[]
for index in range(4):
    descriptor=run(0xC0436F00,0xC375D840,index)
    assert descriptor==0xC375D840+0x18+0x34*index
    result['image_channels'].append({'index':index,'descriptor':hex(descriptor)})
assert result['image_channels'][2]['descriptor']=='0xc375d8c0'
result['byte_counts']=[]
for flag,override,expected in [(0,0,306),(0,123,123),(1,0,512),(1,123,512)]:
    u.mem_write(OBS,bytes(0x30)); put(OBS,17); put(OBS+4,9)
    u.mem_write(OBS+0x24,bytes([flag])); put(OBS+0x28,override)
    source_bytes=run(0xC02DA6E0,OBS)
    gray_bytes=run(0xC02DA750,OBS)
    assert source_bytes==expected and gray_bytes==153
    result['byte_counts'].append({'width':17,'height':9,'layout_flag':flag,'override':override,'source_bytes':source_bytes,'gray_bytes':gray_bytes})
result['proof']='Original firmware instructions in Unicorn. External services, allocator, sleep, output, memory routines and MMIO index stubbed; synthetic ring metadata. No hardware, pixel-content or concurrency proof.'
result['firmware_sha256']=hashlib.sha256(fw).hexdigest()
print(json.dumps(result,indent=2))
