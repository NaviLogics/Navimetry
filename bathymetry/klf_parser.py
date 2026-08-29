from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from collections import Counter, defaultdict
import struct
import numpy as np
import pandas as pd

class KlfParseError(RuntimeError): pass
MAVLINK_NAMES={0:"HEARTBEAT",1:"SYS_STATUS",2:"SYSTEM_TIME",24:"GPS_RAW_INT",30:"ATTITUDE",31:"ATTITUDE_QUATERNION",32:"LOCAL_POSITION_NED",33:"GLOBAL_POSITION_INT",36:"SERVO_OUTPUT_RAW",65:"RC_CHANNELS",74:"VFR_HUD",105:"HIGHRES_IMU",111:"TIMESYNC",141:"ALTITUDE",147:"BATTERY_STATUS",230:"ESTIMATOR_STATUS",241:"VIBRATION",242:"HOME_POSITION",245:"EXTENDED_SYS_STATE"}
@dataclass
class KlfInventory:
    path:str; size_bytes:int; complete_frames:int=0; incomplete_frames:int=0; checksum_errors:int=0; direct_kogger_frames:int=0; mavlink_proxy_frames:int=0; mavlink_v1_frames:int=0; id_counts:dict[int,int]=field(default_factory=dict); acoustic_cycle_starts:int=0; complete_five_fragment_cycles:int=0; sonar_frequency_hz:float|None=None; mavlink_sequence_missing_positions:int=0; mavlink_sequence_gap_events:int=0; direct_um982_stream_detected:bool=False; master_slave_separation_proven:bool=False
@dataclass
class KlfParseResult:
    inventory:KlfInventory; mavlink_inventory:pd.DataFrame; global_position:pd.DataFrame; gps_raw:pd.DataFrame; attitude:pd.DataFrame; frame_records:list[dict]; mavlink_records:list[dict]
def _fletcher_ok(frame:bytes)->bool:
    a=b=0
    for value in frame[2:-2]: a=(a+value)&255; b=(b+a)&255
    return len(frame)>=2 and frame[-2]==a and frame[-1]==b
def _decode_kp2(frame:bytes)->dict:
    opt_len=frame[4]; flags=int.from_bytes(frame[5:7],"little") if opt_len>=3 else 0; idx=7; out={"flags":flags,"is_proxy":bool(flags&1)}
    if flags&2: idx+=2
    if flags&4: out["stream_flags"]=frame[idx]; out["stream_id"]=int.from_bytes(frame[idx+1:idx+3],"little"); out["stream_offset"]=int.from_bytes(frame[idx+3:idx+7],"little"); idx+=7
    if flags&8: out["ltime_ms"]=int.from_bytes(frame[idx:idx+4],"little"); idx+=4
    if (flags>>4)&3: idx+=8
    payload_start=opt_len+4
    if out["is_proxy"]: out["proxy"]=frame[payload_start:-2]
    elif payload_start+3<=len(frame)-2:
        idver=int.from_bytes(frame[payload_start+1:payload_start+3],"little"); out["id"]=idver>>3; out["version"]=idver&7; out["payload"]=frame[payload_start+3:-2]
    return out
def _frequency_stats(times_ms:list[int])->dict:
    if len(times_ms)<2:return {"duration_s":0.0,"mean_frequency_hz":None,"median_interval_s":None,"p95_interval_s":None,"max_interval_s":None}
    t=np.asarray(times_ms,dtype=float)/1000; dt=np.diff(t); duration=float(t[-1]-t[0]); return {"duration_s":duration,"mean_frequency_hz":(len(t)-1)/duration if duration>0 else None,"median_interval_s":float(np.median(dt)),"p95_interval_s":float(np.percentile(dt,95)),"max_interval_s":float(np.max(dt))}
def inspect_klf(path:Path,capture_records:bool=True)->KlfParseResult:
    if not path.exists() or not path.is_file(): raise KlfParseError(f"KLF not found: {path}")
    data=path.read_bytes(); pos=0; frame_index=0; checksum_errors=0; incomplete=0; direct=proxy_count=mav_v1=0; id_counts=Counter(); chart_patterns=Counter(); current_chart=None; ping_times=[]; mav_by_id=defaultdict(list); seqs=[]; gpi=[]; gps=[]; attitude=[]; frame_records=[]; mav_records=[]
    while pos<len(data):
        if pos+4>len(data): incomplete+=1; break
        if data[pos:pos+2]!=b"\xCC\x55":
            nxt=data.find(b"\xCC\x55",pos+1)
            if nxt<0: break
            pos=nxt; continue
        frame_len=int.from_bytes(data[pos+2:pos+4],"little")
        if frame_len<8 or frame_len>65535: pos+=1; continue
        if pos+frame_len>len(data): incomplete+=1; break
        frame=data[pos:pos+frame_len]; checksum_valid=_fletcher_ok(frame); checksum_errors+=0 if checksum_valid else 1; decoded=_decode_kp2(frame); frame_index+=1
        if capture_records: frame_records.append({"frame_index":frame_index,"byte_offset":pos,"frame_type":"proxy" if decoded.get("is_proxy") else "kogger","payload_length":frame_len,"ltime_ms":decoded.get("ltime_ms"),"checksum_valid":checksum_valid,"complete":True,"kogger_id":decoded.get("id")})
        if decoded.get("is_proxy"):
            proxy_count+=1; pr=decoded.get("proxy",b"")
            if len(pr)>=8 and pr[0]==0xFE and len(pr)==pr[1]+8:
                mav_v1+=1; msgid=pr[5]; sequence=pr[2]; sysid=pr[3]; compid=pr[4]; payload=pr[6:6+pr[1]]; ltime=decoded.get("ltime_ms"); px4_boot=None; utc_us=None
                if ltime is not None:mav_by_id[msgid].append(ltime)
                seqs.append(sequence)
                if msgid==33 and len(payload)>=28:
                    tb,lat,lon,alt,rel,vx,vy,vz,hdg=struct.unpack_from("<IiiiihhhH",payload,0); px4_boot=tb; gpi.append((frame_index,ltime,tb,lat/1e7,lon/1e7,alt/1000,vx/100,vy/100,vz/100))
                elif msgid==24 and len(payload)>=30:
                    time_usec,lat,lon,alt,eph,epv,vel,cog,fix,sats=struct.unpack_from("<QiiiHHHHBB",payload,0); gps.append((frame_index,ltime,time_usec,lat/1e7,lon/1e7,alt/1000,eph,epv,vel/100,cog/100,fix,sats))
                elif msgid==30 and len(payload)>=28:
                    tb,roll,pitch,yaw,rs,ps,ys=struct.unpack_from("<Iffffff",payload,0); px4_boot=tb; attitude.append((frame_index,ltime,tb,roll,pitch,yaw))
                elif msgid==2 and len(payload)>=12: utc_us,px4_boot=struct.unpack_from("<QI",payload,0)
                if capture_records:mav_records.append({"frame_index":frame_index,"msgid":msgid,"message":MAVLINK_NAMES.get(msgid,f"ID_{msgid}"),"sequence":sequence,"sysid":sysid,"compid":compid,"ltime_ms":ltime,"px4_boot_time_ms":px4_boot,"utc_time_us":utc_us})
        else:
            direct+=1; kid=decoded.get("id")
            if kid is not None:id_counts[kid]+=1
            if kid==3:
                payload=decoded.get("payload",b"")
                if len(payload)>=6:
                    sequence_offset=int.from_bytes(payload[0:2],"little")
                    if sequence_offset==0:
                        if current_chart is not None:chart_patterns[tuple(current_chart)]+=1
                        current_chart=[]
                        if decoded.get("ltime_ms") is not None:ping_times.append(decoded["ltime_ms"])
                    if current_chart is not None:current_chart.append(sequence_offset)
        pos+=frame_len
    if current_chart is not None:chart_patterns[tuple(current_chart)]+=1
    gap_sizes=[(b-a)%256-1 for a,b in zip(seqs[:-1],seqs[1:]) if (b-a)%256>1]
    rows=[{"msgid":msgid,"message":MAVLINK_NAMES.get(msgid,f"ID_{msgid}"),"count":len(times),**_frequency_stats(times)} for msgid,times in sorted(mav_by_id.items())]
    sonar_stats=_frequency_stats(ping_times); inventory=KlfInventory(str(path),len(data),frame_index,incomplete,checksum_errors,direct,proxy_count,mav_v1,dict(id_counts),len(ping_times),chart_patterns.get((0,200,400,600,800),0),sonar_stats["mean_frequency_hz"],int(sum(gap_sizes)),len(gap_sizes),False,False)
    gpi_df=pd.DataFrame(gpi,columns=["frame_index","kogger_ltime_ms","px4_boot_time_ms","latitude_deg","longitude_deg","altitude_m","vx_mps","vy_mps","vz_mps"]); gps_df=pd.DataFrame(gps,columns=["frame_index","kogger_ltime_ms","time_usec","latitude_deg","longitude_deg","altitude_m","eph","epv","velocity_mps","cog_deg","fix_type","satellites"]); att_df=pd.DataFrame(attitude,columns=["frame_index","kogger_ltime_ms","px4_boot_time_ms","roll_rad","pitch_rad","yaw_rad"])
    return KlfParseResult(inventory,pd.DataFrame(rows),gpi_df,gps_df,att_df,frame_records,mav_records)
