from __future__ import annotations
import numpy as np
import pandas as pd


def _nearest_indices(times: np.ndarray, query: np.ndarray) -> tuple[np.ndarray,np.ndarray]:
    if len(times)==0:
        return np.full(len(query),-1,dtype=int),np.full(len(query),np.nan)
    order=np.argsort(times); t=times[order]
    pos=np.searchsorted(t,query); right=np.clip(pos,0,len(t)-1); left=np.clip(pos-1,0,len(t)-1)
    choose_left=np.abs(query-t[left])<=np.abs(t[right]-query); chosen=np.where(choose_left,left,right)
    idx=order[chosen]; return idx,np.abs(times[idx]-query)


def _source_cycle_base(csv_obs:pd.DataFrame)->tuple[np.ndarray,str]:
    """Prefer the recorder-export Number field so edited/subset CSV exports retain KLF identity."""
    rows=np.arange(len(csv_obs),dtype=np.int64)
    if "csv_number" not in csv_obs:
        return rows,"row_index"
    numbers=pd.to_numeric(csv_obs["csv_number"],errors="coerce").to_numpy(float)
    finite=np.isfinite(numbers)
    integerish=finite & (np.abs(numbers-np.rint(numbers))<1e-6)
    if integerish.mean()<0.95:
        return rows,"row_index"
    base=rows.copy(); base[integerish]=np.rint(numbers[integerish]).astype(np.int64)
    return base,"csv_number"


def _alignment_score(csv_obs:pd.DataFrame,sonar:pd.DataFrame,gpi:pd.DataFrame,base_index:np.ndarray,offset:int)->tuple[float,int]:
    if sonar.empty or gpi.empty:return 0.0,0
    sidx=base_index+offset; valid=(sidx>=0)&(sidx<len(sonar))
    valid &= csv_obs["latitude_raw_deg"].notna().to_numpy() & csv_obs["longitude_raw_deg"].notna().to_numpy()
    valid &= ~((csv_obs["latitude_raw_deg"].to_numpy()==0)&(csv_obs["longitude_raw_deg"].to_numpy()==0))
    use=np.where(valid)[0]
    if len(use)>5000: use=use[np.linspace(0,len(use)-1,5000,dtype=int)]
    if not len(use):return 0.0,0
    q=sonar.iloc[sidx[use]]["kogger_ltime_ms"].to_numpy(float); gi,_=_nearest_indices(gpi["kogger_ltime_ms"].to_numpy(float),q)
    glat=gpi.iloc[gi]["latitude_deg"].to_numpy(float); glon=gpi.iloc[gi]["longitude_deg"].to_numpy(float)
    clat=csv_obs.iloc[use]["latitude_raw_deg"].to_numpy(float); clon=csv_obs.iloc[use]["longitude_raw_deg"].to_numpy(float)
    exact=np.isclose(clat,glat,atol=5e-7,rtol=0)&np.isclose(clon,glon,atol=5e-7,rtol=0)
    return float(exact.mean()),int(len(use))


def _attach_nearest(result:pd.DataFrame,query_times:np.ndarray,df:pd.DataFrame,prefix:str,columns:list[str],max_age_ms:float|None=None)->None:
    if df is None or df.empty:return
    idx,age=_nearest_indices(df["kogger_ltime_ms"].to_numpy(float),query_times); ok=idx>=0
    if max_age_ms is not None:ok &= age<=max_age_ms
    result[f"{prefix}_age_ms"]=np.where(ok,age,np.nan)
    for col in columns:
        values=np.full(len(result),np.nan,dtype=object if df[col].dtype==object else float)
        if ok.any():values[ok]=df.iloc[idx[ok]][col].to_numpy()
        result[f"{prefix}_{col}"]=values


def match_csv_to_klf(csv_obs:pd.DataFrame,global_position:pd.DataFrame,sonar_cycles:pd.DataFrame|None=None,gps_raw:pd.DataFrame|None=None,attitude:pd.DataFrame|None=None,estimator_status:pd.DataFrame|None=None,system_time:pd.DataFrame|None=None)->tuple[pd.DataFrame,dict]:
    result=pd.DataFrame(index=csv_obs.index)
    result["global_position_ref"]=pd.Series(pd.array([None]*len(csv_obs),dtype="Int64"),index=csv_obs.index)
    result["px4_boot_time_ms"]=np.nan; result["kogger_time_ms"]=np.nan; result["match_method"]="unmatched"; result["match_confidence"]="low"; result["match_ambiguous"]=False; result["match_residual_m"]=np.nan
    if global_position.empty:return result,{"matched_gpi":0,"gpi_count":0,"match_ratio":0.0,"method":"unavailable"}
    if sonar_cycles is None or sonar_cycles.empty:
        return _legacy_coordinate_match(csv_obs,global_position,result)

    base_index,index_source=_source_cycle_base(csv_obs)
    candidates=[]
    for offset in range(-3,4):
        score,n=_alignment_score(csv_obs,sonar_cycles,global_position,base_index,offset); candidates.append((score,n,offset))
    candidates.sort(reverse=True); best_score,n,best_offset=candidates[0]; second=candidates[1][0] if len(candidates)>1 else 0.0
    ambiguous=(best_score<0.50) or (best_score-second<0.02)
    sidx=base_index+best_offset; mapped=(sidx>=0)&(sidx<len(sonar_cycles))
    q=np.full(len(csv_obs),np.nan); q[mapped]=sonar_cycles.iloc[sidx[mapped]]["kogger_ltime_ms"].to_numpy(float); result.loc[mapped,"kogger_time_ms"]=q[mapped]
    gi,gage=_nearest_indices(global_position["kogger_ltime_ms"].to_numpy(float),q[mapped]); g_ok=gage<=250.0; mapped_rows=np.where(mapped)[0]; good_rows=mapped_rows[g_ok]; good_gi=gi[g_ok]
    if len(good_rows):
        gp=global_position.iloc[good_gi]; result.loc[good_rows,"global_position_ref"]=pd.array(gp["frame_index"].to_numpy(),dtype="Int64"); result.loc[good_rows,"px4_boot_time_ms"]=gp["px4_boot_time_ms"].to_numpy(float); result.loc[good_rows,"gpi_latitude_deg"]=gp["latitude_deg"].to_numpy(float); result.loc[good_rows,"gpi_longitude_deg"]=gp["longitude_deg"].to_numpy(float); result.loc[good_rows,"gpi_age_ms"]=gage[g_ok]
    result.loc[mapped,"match_method"]="sonar_cycle_kogger_ltime"; result.loc[mapped,"match_confidence"]="medium" if ambiguous else "high"; result.loc[mapped,"match_ambiguous"]=ambiguous
    _attach_nearest(result,q,gps_raw,"gps",["fix_type","satellites","eph","epv","latitude_deg","longitude_deg"],250.0)
    _attach_nearest(result,q,attitude,"attitude",["roll_rad","pitch_rad","yaw_rad"],250.0)
    _attach_nearest(result,q,estimator_status,"estimator",["pos_horiz_accuracy_m","pos_vert_accuracy_m","solution_status_flags"],15000.0)
    if system_time is not None and not system_time.empty:
        _attach_nearest(result,q,system_time,"system_time",["time_unix_usec","px4_boot_time_ms"],15000.0)
        if "system_time_time_unix_usec" in result:
            utc=[]
            for v in result["system_time_time_unix_usec"]:
                utc.append(pd.to_datetime(v,unit="us",utc=True,errors="coerce") if pd.notna(v) and float(v)>1e15 else pd.NaT)
            result["timestamp_utc_from_system_time"]=utc
    report={"method":"sonar_cycle_kogger_ltime","csv_rows":len(csv_obs),"sonar_cycles":len(sonar_cycles),"cycle_index_source":index_source,"selected_index_to_cycle_offset":best_offset,"alignment_coordinate_agreement":best_score,"alignment_samples":n,"alignment_second_best":second,"alignment_ambiguous":ambiguous,"matched_gpi":int(len(good_rows)),"gpi_count":len(global_position),"match_ratio":float(len(good_rows)/max(1,len(csv_obs))),"identity_confidence":"medium" if ambiguous else "high","gpi_max_age_ms":250.0,"gps_max_age_ms":250.0,"attitude_max_age_ms":250.0,"estimator_max_age_ms":15000.0}
    return result,report


def _legacy_coordinate_match(csv_obs:pd.DataFrame,global_position:pd.DataFrame,result:pd.DataFrame)->tuple[pd.DataFrame,dict]:
    csv_keys=list(zip(csv_obs["latitude_raw_deg"].round(7),csv_obs["longitude_raw_deg"].round(7))); gpi_keys=list(zip(global_position["latitude_deg"].round(7),global_position["longitude_deg"].round(7))); j=0; matched=0
    for i,key in enumerate(csv_keys):
        if key==(0.0,0.0) or any(pd.isna(v) for v in key):continue
        if j<len(gpi_keys) and key==gpi_keys[j]:
            row=global_position.iloc[j]; result.at[i,"global_position_ref"]=int(row["frame_index"]); result.at[i,"px4_boot_time_ms"]=float(row["px4_boot_time_ms"]); result.at[i,"kogger_time_ms"]=float(row["kogger_ltime_ms"]); result.at[i,"gpi_latitude_deg"]=float(row["latitude_deg"]); result.at[i,"gpi_longitude_deg"]=float(row["longitude_deg"]); result.at[i,"match_method"]="exact_coordinate_sequence"; result.at[i,"match_confidence"]="high"; j+=1; matched+=1
        elif j>0 and key==gpi_keys[j-1]:
            row=global_position.iloc[j-1]; result.at[i,"global_position_ref"]=int(row["frame_index"]); result.at[i,"px4_boot_time_ms"]=float(row["px4_boot_time_ms"]); result.at[i,"kogger_time_ms"]=float(row["kogger_ltime_ms"]); result.at[i,"gpi_latitude_deg"]=float(row["latitude_deg"]); result.at[i,"gpi_longitude_deg"]=float(row["longitude_deg"]); result.at[i,"match_method"]="held_coordinate_state"; result.at[i,"match_confidence"]="medium"; result.at[i,"match_ambiguous"]=True
    ratio=matched/max(1,len(gpi_keys)); return result,{"matched_gpi":matched,"gpi_count":len(gpi_keys),"match_ratio":ratio,"method":"legacy_coordinate_sequence","identity_confidence":"high" if ratio>=.99 else "medium" if ratio>=.9 else "low"}
