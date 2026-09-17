from __future__ import annotations
import numpy as np
import pandas as pd


def _fit(x:np.ndarray,y:np.ndarray)->dict:
    const=float(np.median(y-x)); rc=y-(x+const); a,b=np.linalg.lstsq(np.column_stack([x,np.ones_like(x)]),y,rcond=None)[0]; rl=y-(a*x+b)
    def stats(r):return {"rmse_ms":float(np.sqrt(np.mean(r*r))),"median_abs_error_ms":float(np.median(np.abs(r))),"p95_abs_error_ms":float(np.percentile(np.abs(r),95)),"max_abs_error_ms":float(np.max(np.abs(r)))}
    cs=stats(rc); ls=stats(rl); linear=ls["rmse_ms"]<cs["rmse_ms"]*.8
    return {"model_type":"linear" if linear else "constant_offset","clock_scale":float(a) if linear else 1.0,"clock_offset_ms":float(b) if linear else const,"valid_from_kogger_ms":float(x.min()),"valid_to_kogger_ms":float(x.max()),"constant_model":{"offset_ms":const,**cs},"linear_model":{"scale":float(a),"offset_ms":float(b),**ls},"quality":"derived"}

def build_clock_model(attitude:pd.DataFrame)->dict:
    required={"kogger_ltime_ms","px4_boot_time_ms"}
    if attitude.empty or not required.issubset(attitude.columns) or len(attitude)<10:return {"model_type":"unavailable","quality":"unavailable"}
    df=attitude[["kogger_ltime_ms","px4_boot_time_ms"]].dropna().sort_values("kogger_ltime_ms").reset_index(drop=True)
    if len(df)<10:return {"model_type":"unavailable","quality":"unavailable"}
    boot=df["px4_boot_time_ms"].to_numpy(float); reset=np.r_[True,np.diff(boot)<-1000.0]; seg=np.cumsum(reset)-1; models=[]
    for sid in np.unique(seg):
        part=df.iloc[np.where(seg==sid)[0]]
        if len(part)<10:continue
        m=_fit(part["kogger_ltime_ms"].to_numpy(float),part["px4_boot_time_ms"].to_numpy(float)); m["segment_id"]=int(sid); m["samples"]=int(len(part)); models.append(m)
    if not models:return {"model_type":"unavailable","quality":"unavailable"}
    if len(models)==1:return models[0]
    return {"model_type":"segmented_px4_boot_clock","quality":"derived","reboot_count":len(models)-1,"segments":models,"note":"Relative sonar/MAVLink association uses shared Kogger ltime_ms and does not cross-fit PX4 boot resets."}
