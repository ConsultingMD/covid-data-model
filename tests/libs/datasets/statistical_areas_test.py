import dataclasses

import pytest
import pandas as pd
from covidactnow.datapublic.common_fields import CommonFields

from libs.datasets import statistical_areas
from libs.datasets.timeseries import MultiRegionDataset

from libs.pipeline import Region
from tests.dataset_utils_test import read_csv_and_index_fips_date
from tests import test_helpers


def test_load_from_local_public_data():
    agg = statistical_areas.CountyToCBSAAggregator.from_local_public_data()
    agg = dataclasses.replace(agg, aggregations=[])  # Disable scaled aggregations

    assert agg.cbsa_title_map["43580"] == "Sioux City, IA-NE-SD"
    assert agg.county_map["48187"] == "41700"

    df_in = read_csv_and_index_fips_date(
        "fips,state,aggregate_level,county,m1,date,foo\n"
        "48059,ZZ,county,North County,3,2020-05-03,33\n"
        "48253,ZZ,county,South County,4,2020-05-03,77\n"
        "48441,ZZ,county,Other County,2,2020-05-03,41\n"
    ).reset_index()
    ts_in = MultiRegionDataset.from_fips_timeseries_df(df_in)
    ts_out = agg.aggregate(ts_in)
    ts_cbsa = ts_out.get_one_region(Region.from_cbsa_code("10180"))
    assert ts_cbsa.date_indexed["m1"].to_dict() == {
        pd.to_datetime("2020-05-03"): 9,
    }


def test_aggregate():
    df_in = read_csv_and_index_fips_date(
        "fips,state,aggregate_level,county,m1,date,foo\n"
        "55005,ZZ,county,North County,1,2020-05-01,11\n"
        "55005,ZZ,county,North County,2,2020-05-02,22\n"
        "55005,ZZ,county,North County,3,2020-05-03,33\n"
        "55005,ZZ,county,North County,0,2020-05-04,0\n"
        "55006,ZZ,county,South County,0,2020-05-01,0\n"
        "55006,ZZ,county,South County,0,2020-05-02,0\n"
        "55006,ZZ,county,South County,3,2020-05-03,44\n"
        "55006,ZZ,county,South County,4,2020-05-04,55\n"
        "55,ZZ,state,Grand State,41,2020-05-01,66\n"
        "55,ZZ,state,Grand State,43,2020-05-03,77\n"
    ).reset_index()
    ts_in = MultiRegionDataset.from_fips_timeseries_df(df_in)
    agg = statistical_areas.CountyToCBSAAggregator(
        county_map={"55005": "10001", "55006": "10001"},
        cbsa_title_map={"10001": "Stat Area 1"},
        aggregations=[],
    )
    ts_out = agg.aggregate(ts_in)

    assert ts_out.groupby_region().ngroups == 1

    ts_cbsa = ts_out.get_one_region(Region.from_cbsa_code("10001"))
    assert ts_cbsa.date_indexed["m1"].to_dict() == {
        pd.to_datetime("2020-05-01"): 1,
        pd.to_datetime("2020-05-02"): 2,
        pd.to_datetime("2020-05-03"): 6,
        pd.to_datetime("2020-05-04"): 4,
    }


@pytest.mark.parametrize("reporting_ratio,expected_na", [(0.8, False), (0.91, True)])
def test_aggregate_reporting_ratio(reporting_ratio, expected_na):
    ny_region = Region.from_state("NY")
    az_region = Region.from_state("AZ")
    us_region = Region.from_iso1("us")
    aggregate_map = {
        ny_region: us_region,
        az_region: us_region,
    }
    metrics = {ny_region: {CommonFields.CASES: [100]}, az_region: {CommonFields.CASES: [None]}}
    static = {ny_region: {CommonFields.POPULATION: 900}, az_region: {CommonFields.POPULATION: 100}}
    dataset = test_helpers.build_dataset(metrics, static_by_region_then_field_name=static)

    agg = statistical_areas.CountyToCBSAAggregator(
        county_map={ny_region.fips: us_region.fips, az_region.fips: us_region.fips},
        cbsa_title_map={us_region.fips: "Stat Area 1"},
        aggregations=[],
    )
    aggregation = agg.aggregate(dataset, reporting_ratio_required_to_aggregate=reporting_ratio)
    cases = aggregation.timeseries[CommonFields.CASES]
    if expected_na:
        assert not len(cases)
    else:
        assert len(cases)
