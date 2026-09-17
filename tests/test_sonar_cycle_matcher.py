import pandas as pd
from bathymetry.matcher import match_csv_to_klf


def test_sonar_cycle_alignment_and_quality_attachment():
    csv=pd.DataFrame({"latitude_raw_deg":[0,55.0,55.000001,55.000002],"longitude_raw_deg":[0,37.0,37.000001,37.000002]})
    sonar=pd.DataFrame({"sonar_cycle_index":[0,1,2],"frame_index":[1,2,3],"kogger_ltime_ms":[1000,1100,1200]})
    gpi=pd.DataFrame({"frame_index":[10,11,12],"kogger_ltime_ms":[1000,1100,1200],"px4_boot_time_ms":[500,600,700],"latitude_deg":[55.0,55.000001,55.000002],"longitude_deg":[37.0,37.000001,37.000002]})
    gps=pd.DataFrame({"kogger_ltime_ms":[1000,1100,1200],"fix_type":[3,3,3],"satellites":[28,28,28],"eph":[50,50,50],"epv":[80,80,80],"latitude_deg":[55.0,55.000001,55.000002],"longitude_deg":[37.0,37.000001,37.000002]})
    att=pd.DataFrame({"kogger_ltime_ms":[1000,1100,1200],"roll_rad":[0.,0.,0.],"pitch_rad":[0.,0.,0.],"yaw_rad":[1.,1.,1.]})
    est=pd.DataFrame({"kogger_ltime_ms":[1000,1100,1200],"pos_horiz_accuracy_m":[.4,.4,.4],"pos_vert_accuracy_m":[.3,.3,.3],"solution_status_flags":[831,831,831]})
    out,report=match_csv_to_klf(csv,gpi,sonar,gps,att,est,None)
    assert report["cycle_index_source"]=="row_index"
    assert report["selected_index_to_cycle_offset"]==-1
    assert report["alignment_coordinate_agreement"]==1.0
    assert out.loc[1,"gps_fix_type"]==3
    assert out.loc[1,"attitude_age_ms"]==0
    assert out.loc[1,"estimator_pos_horiz_accuracy_m"]==.4


def test_edited_subset_uses_csv_number_to_preserve_original_cycle_identity():
    sonar=pd.DataFrame({"sonar_cycle_index":range(8),"frame_index":range(100,108),"kogger_ltime_ms":[1000,1100,1200,1300,1400,1500,1600,1700]})
    gpi=pd.DataFrame({"frame_index":range(200,208),"kogger_ltime_ms":[1000,1100,1200,1300,1400,1500,1600,1700],"px4_boot_time_ms":[500,600,700,800,900,1000,1100,1200],"latitude_deg":[55+i*1e-6 for i in range(8)],"longitude_deg":[37+i*1e-6 for i in range(8)]})
    csv=pd.DataFrame({"csv_number":[3,4,5],"latitude_raw_deg":[55.000003,55.000004,55.000005],"longitude_raw_deg":[37.000003,37.000004,37.000005]})
    out,report=match_csv_to_klf(csv,gpi,sonar)
    assert report["cycle_index_source"]=="csv_number"
    assert report["selected_index_to_cycle_offset"]==0
    assert report["alignment_coordinate_agreement"]==1.0
    assert list(out["kogger_time_ms"])==[1300.,1400.,1500.]
    assert report["matched_gpi"]==3


def test_alignment_survives_px4_boot_reset_because_kogger_time_is_primary():
    csv=pd.DataFrame({"latitude_raw_deg":[1.,1.1],"longitude_raw_deg":[2.,2.1]})
    sonar=pd.DataFrame({"sonar_cycle_index":[0,1],"frame_index":[1,2],"kogger_ltime_ms":[1000,2000]})
    gpi=pd.DataFrame({"frame_index":[10,11],"kogger_ltime_ms":[1000,2000],"px4_boot_time_ms":[900,100],"latitude_deg":[1.,1.1],"longitude_deg":[2.,2.1]})
    out,report=match_csv_to_klf(csv,gpi,sonar)
    assert report["matched_gpi"]==2
    assert list(out["px4_boot_time_ms"].dropna())==[900.,100.]
