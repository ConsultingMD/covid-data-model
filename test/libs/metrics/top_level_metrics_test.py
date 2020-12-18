from typing import List
import dataclasses

import numpy as np
import pandas as pd
import pytest
import structlog
from freezegun import freeze_time
from covidactnow.datapublic.common_fields import CommonFields

from api import can_api_v2_definition
from libs.metrics import top_level_metrics
from libs.datasets.timeseries import MultiRegionDataset
from libs.datasets.timeseries import OneRegionTimeseriesDataset
from libs.pipeline import Region

from test.dataset_utils_test import read_csv_and_index_fips_date

# Columns used for building input dataframes in tests. It covers the fields
# required to run top level metrics without error.
INPUT_COLUMNS = [
    "new_cases",
    "cases",
    "test_positivity",
    "positive_tests",
    "negative_tests",
    "contact_tracers_count",
    "current_icu",
    "icu_beds",
    "current_icu_total",
]


def _build_timeseries_dataframe(
    fips: str,
    all_columns: List[str],
    start_date: str = None,
    dates: List[str] = None,
    **column_data,
) -> pd.DataFrame:
    """Build a timeseries dataframe with fips, date and specified columns.

    Args:
        fips: Fips code for region.
        all_columns: All columns to be included in output dataframe.
        start_date: Optional start date.
        dates: Optional list of dates to use for rows, length of dates must match
            length of data in column_data.
        column_data: Column data with values.  Names of variables should match columns
            in `all_columns`. All lists must be the same length.

    Returns: DataFrame.
    """
    data = {column: column_data.get(column, np.nan) for column in all_columns}

    max_len = max(len(value) for value in data.values() if isinstance(value, list))

    if dates:
        dates = [pd.Timestamp(date) for date in dates]
    elif start_date:
        dates = pd.date_range(start_date, periods=max_len, freq="D")
    else:
        raise ValueError("Must set either `start_date` or `dates`")

    return pd.DataFrame({"date": dates, "fips": fips, **data})


def _build_metrics_df(fips, start_date=None, dates=None, **metrics_data) -> pd.DataFrame:
    """Builds a dataframe that has same structure as those built by
    `top_level_metrics.calculate_metrics_for_timeseries`
    """
    metrics = [
        "caseDensity",
        "testPositivityRatio",
        "contactTracerCapacityRatio",
        "infectionRate",
        "infectionRateCI90",
        "icuHeadroomRatio",
        "icuCapacityRatio",
    ]
    return _build_timeseries_dataframe(
        fips, metrics, start_date=start_date, dates=dates, **metrics_data
    )


def _series_with_date_index(data, date: str = "2020-08-25", **series_kwargs):
    date_series = pd.date_range(date, periods=len(data), freq="D")
    return pd.Series(data, index=date_series, **series_kwargs)


def _fips_csv_to_one_region(
    csv_str: str, region: Region, latest=None
) -> OneRegionTimeseriesDataset:
    df = read_csv_and_index_fips_date(csv_str).reset_index()
    # from_timeseries_and_latest adds the location_id column needed by get_one_region
    dataset = MultiRegionDataset.from_fips_timeseries_df(df).get_one_region(region)
    if latest:
        return dataclasses.replace(dataset, latest=latest)
    else:
        return dataset


def _fips_df_to_one_region(df, region: Region, latest=None) -> OneRegionTimeseriesDataset:
    # from_timeseries_and_latest adds the location_id column needed by get_one_region
    dataset = MultiRegionDataset.from_fips_timeseries_df(df).get_one_region(region)
    if latest:
        return dataclasses.replace(dataset, latest=latest)
    else:
        return dataset


def test_calculate_case_density():
    """
    It should use population, smoothing and a normalizing factor to calculate case density.
    """
    cases = _series_with_date_index([0, 0, 20, 40, 60])
    pop = 100
    every_ten = 10
    smooth = 2

    density = top_level_metrics.calculate_case_density(
        cases, pop, smooth=smooth, normalize_by=every_ten
    )

    pd.testing.assert_series_equal(
        density, _series_with_date_index([0.0, 0.0, 1.0, 3.0, 5.0], dtype="float"),
    )


def test_calculate_test_positivity():
    """
    It should use smoothed case data to calculate positive test rate.
    """

    positive_tests = _series_with_date_index([0, 1, 3, 6])
    negative_tests = _series_with_date_index([0, 0, 1, 3])
    positive_rate = top_level_metrics.calculate_test_positivity(
        positive_tests, negative_tests, smooth=2, lag_lookback=1
    )
    expected = _series_with_date_index([np.nan, 1, 0.75, 2 / 3], dtype="float64")
    pd.testing.assert_series_equal(positive_rate, expected)


def test_calculate_test_positivity_lagging():
    """
    It should return an empty series if there is missing negative case data.
    """
    positive_tests = _series_with_date_index([0, 1, 2, 4, 8])
    negative_tests = _series_with_date_index([0, 0, 1, 2, 2])
    positive_rate = top_level_metrics.calculate_test_positivity(
        positive_tests, negative_tests, smooth=2, lag_lookback=1
    )
    pd.testing.assert_series_equal(positive_rate, pd.Series([], dtype="float64"))


def test_calculate_test_positivity_extra_day():
    """
    It should return an empty series if there is missing negative case data.
    """
    positive_tests = _series_with_date_index([0, 4, np.nan])
    negative_tests = _series_with_date_index([0, 4, np.nan])
    positive_rate = top_level_metrics.calculate_test_positivity(positive_tests, negative_tests)
    expected = _series_with_date_index([np.nan, 0.5], dtype="float64")
    pd.testing.assert_series_equal(positive_rate, expected)


def test_top_level_metrics_basic():
    data = (
        "date,fips,cases,new_cases,positive_tests,negative_tests,contact_tracers_count"
        ",current_icu,current_icu_total,icu_beds\n"
        "2020-08-17,36,10,10,10,90,1,10,20,\n"
        "2020-08-18,36,20,10,20,180,2,10,20,\n"
        "2020-08-19,36,,,,,3,10,20,\n"
        "2020-08-20,36,40,,40,360,4,10,20,\n"
    )
    latest = {
        CommonFields.POPULATION: 100_000,
        CommonFields.FIPS: "36",
        CommonFields.STATE: "NY",
        CommonFields.ICU_TYPICAL_OCCUPANCY_RATE: 0.5,
        CommonFields.ICU_BEDS: 30,
    }
    one_region = _fips_csv_to_one_region(data, Region.from_fips("36"), latest=latest)
    results, _ = top_level_metrics.calculate_metrics_for_timeseries(
        one_region, None, None, structlog.get_logger(), require_recent_icu_data=False
    )

    expected = _build_metrics_df(
        "36",
        start_date="2020-08-17",
        caseDensity=[10, 10, None, None],
        testPositivityRatio=[None, 0.1, 0.1, 0.1],
        contactTracerCapacityRatio=[0.02, 0.04, None, None],
        icuHeadroomRatio=[0.5, 0.5, 0.5, 0.5],
    )
    pd.testing.assert_frame_equal(expected, results)


def test_top_level_metrics_incomplete_latest():
    # This test doesn't have ICU_BEDS set in `latest`. It checks that the metrics are still built.
    data = (
        "date,fips,new_cases,cases,positive_tests,negative_tests,contact_tracers_count"
        ",current_icu,current_icu_total,icu_beds\n"
        "2020-08-17,36,10,10,10,90,1,10,20,\n"
        "2020-08-18,36,10,20,20,180,2,10,20,\n"
        "2020-08-19,36,10,,,,3,10,20,\n"
        "2020-08-20,36,10,40,40,360,4,10,20,\n"
    )
    latest = {
        CommonFields.POPULATION: 100_000,
        CommonFields.FIPS: "36",
        CommonFields.STATE: "NY",
        # ICU_BEDS not set
    }
    one_region = _fips_csv_to_one_region(data, Region.from_fips("36"), latest=latest)
    results, _ = top_level_metrics.calculate_metrics_for_timeseries(
        one_region, None, None, structlog.get_logger(), require_recent_icu_data=False
    )

    expected = _build_metrics_df(
        "36",
        start_date="2020-08-17",
        caseDensity=[10, 10, 10, 10],
        testPositivityRatio=[None, 0.1, 0.1, 0.1],
        contactTracerCapacityRatio=[0.02, 0.04, 0.06, 0.08],
    )
    pd.testing.assert_frame_equal(expected, results, check_dtype=False)


def test_top_level_metrics_no_pos_neg_tests_no_positivity_ratio():
    # All of positive_tests, negative_tests are empty and test_positivity is absent. Make sure
    # other metrics are still produced.
    data = (
        "date,fips,new_cases,cases,positive_tests,negative_tests,contact_tracers_count,current_icu,icu_beds\n"
        "2020-08-17,36,10.0,10.0,,,1,,\n"
        "2020-08-18,36,10.0,20.0,,,2,,\n"
        "2020-08-19,36,10.0,30.0,,,3,,\n"
        "2020-08-20,36,10.0,40.0,,,4,,\n"
    )
    latest = {
        CommonFields.POPULATION: 100_000,
        CommonFields.FIPS: "36",
        CommonFields.STATE: "NY",
        CommonFields.ICU_BEDS: 10,
    }
    one_region = _fips_csv_to_one_region(data, Region.from_fips("36"), latest=latest)
    results, _ = top_level_metrics.calculate_metrics_for_timeseries(
        one_region, None, None, structlog.get_logger()
    )

    expected = _build_metrics_df(
        "36",
        start_date="2020-08-17",
        caseDensity=[10.0, 10.0, 10.0, 10.0],
        contactTracerCapacityRatio=[0.02, 0.04, 0.06, 0.08],
    )
    pd.testing.assert_frame_equal(expected, results)


def test_top_level_metrics_no_pos_neg_tests_has_positivity_ratio():
    # All of positive_tests, negative_tests are empty. test_positivity has a real value. Make sure
    # test_positivity is copied to the output and other metrics are produced.
    data = (
        "date,fips,new_cases,cases,test_positivity,positive_tests,negative_tests,contact_tracers_count,current_icu,icu_beds\n"
        "2020-08-17,36,10,10,0.02,,,1,,\n"
        "2020-08-18,36,10,20,0.03,,,2,,\n"
        "2020-08-19,36,10,30,0.04,,,3,,\n"
        "2020-08-20,36,10,40,0.05,,,4,,\n"
    )
    latest = {
        CommonFields.POPULATION: 100_000,
        CommonFields.FIPS: "36",
        CommonFields.STATE: "NY",
        CommonFields.ICU_BEDS: 10,
    }
    one_region = _fips_csv_to_one_region(data, Region.from_fips("36"), latest=latest)
    results, _ = top_level_metrics.calculate_metrics_for_timeseries(
        one_region, None, None, structlog.get_logger()
    )

    expected = _build_metrics_df(
        "36",
        start_date="2020-08-17",
        caseDensity=[10, 10, 10, 10],
        testPositivityRatio=[0.02, 0.03, 0.04, 0.05],
        contactTracerCapacityRatio=[0.02, 0.04, 0.06, 0.08],
    )
    pd.testing.assert_frame_equal(expected, results, check_dtype=False)


@pytest.mark.parametrize("pos_neg_tests_recent", [False, True])
def test_top_level_metrics_recent_pos_neg_tests_has_positivity_ratio(pos_neg_tests_recent):
    # positive_tests and negative_tests appear on 8/10 and 8/11. They will be used when
    # that is within 10 days of 'today'.
    data = (
        "date,fips,new_cases,cases,test_positivity,positive_tests,negative_tests,contact_tracers_count,current_icu,icu_beds\n"
        "2020-08-10,36,10,10,0.02,1,10,1,,\n"
        "2020-08-11,36,10,20,0.03,2,20,2,,\n"
        "2020-08-12,36,10,30,0.04,,,3,,\n"
        "2020-08-13,36,10,40,0.05,,,4,,\n"
        "2020-08-14,36,10,50,0.06,,,4,,\n"
        "2020-08-15,36,10,60,0.07,,,4,,\n"
    )
    latest = {
        CommonFields.POPULATION: 100_000,
        CommonFields.FIPS: "36",
        CommonFields.STATE: "NY",
        CommonFields.ICU_BEDS: 10,
    }
    one_region = _fips_csv_to_one_region(data, Region.from_fips("36"), latest=latest)

    if pos_neg_tests_recent:
        freeze_date = "2020-08-21"
        # positive_tests and negative_tests are used
        expected = _build_metrics_df(
            "36",
            start_date="2020-08-10",
            caseDensity=[10, 10, 10, 10, 10, 10],
            testPositivityRatio=[None, 0.0909, None, None, None, None],
            contactTracerCapacityRatio=[0.02, 0.04, 0.06, 0.08, 0.08, 0.08],
        )

    else:
        freeze_date = "2020-08-22"
        # positive_tests and negative_tests no longer recent so test_positivity is copied to output.
        expected = _build_metrics_df(
            "36",
            start_date="2020-08-10",
            caseDensity=[10, 10, 10, 10, 10, 10],
            testPositivityRatio=[0.02, 0.03, 0.04, 0.05, 0.06, 0.07],
            contactTracerCapacityRatio=[0.02, 0.04, 0.06, 0.08, 0.08, 0.08],
        )

    with freeze_time(freeze_date):
        results, _ = top_level_metrics.calculate_metrics_for_timeseries(
            one_region, None, None, structlog.get_logger()
        )

    # check_less_precise so only 3 digits need match for testPositivityRatio
    pd.testing.assert_frame_equal(expected, results, check_less_precise=True, check_dtype=False)


def test_top_level_metrics_with_rt():
    region = Region.from_fips("36")
    latest = {
        CommonFields.POPULATION: 100_000,
        CommonFields.FIPS: "36",
        CommonFields.STATE: "NY",
        CommonFields.ICU_TYPICAL_OCCUPANCY_RATE: 0.5,
        CommonFields.ICU_BEDS: 25,
    }
    data = (
        "date,fips,new_cases,positive_tests,negative_tests,contact_tracers_count"
        ",current_icu,current_icu_total,icu_beds\n"
        "2020-08-17,36,,10,90,1,,,\n"
        "2020-08-18,36,10,20,180,2,,,\n"
        "2020-08-19,36,,,,3,,,\n"
        "2020-08-20,36,,40,360,4,,,\n"
    )
    one_region = _fips_csv_to_one_region(data, region, latest=latest)

    data = (
        "date,fips,Rt_MAP_composite,Rt_ci95_composite\n"
        "2020-08-17,36,1.1,1.2\n"
        "2020-08-18,36,1.2,1.3\n"
        "2020-08-19,36,1.1,1.3\n"
        "2020-08-20,36,1.1,1.2\n"
    )
    rt_data = _fips_csv_to_one_region(data, region)

    results, _ = top_level_metrics.calculate_metrics_for_timeseries(
        one_region, rt_data, None, structlog.get_logger()
    )
    expected = _build_metrics_df(
        "36",
        start_date="2020-08-17",
        caseDensity=[0, 5, None, None],
        testPositivityRatio=[None, 0.1, 0.1, 0.1],
        contactTracerCapacityRatio=[None, 0.08, None, None],
        infectionRate=[1.1, 1.2, 1.1, 1.1],
        infectionRateCI90=[0.1, 0.1, 0.2, 0.1],
    )
    pd.testing.assert_frame_equal(expected, results)


def test_calculate_contact_tracers():

    cases = _series_with_date_index([0.0, 1.0, 3.0])
    contact_tracers = _series_with_date_index([5, 5, 5])

    results = top_level_metrics.calculate_contact_tracers(cases, contact_tracers)
    expected = _series_with_date_index([np.nan, 2.0, 0.75])
    pd.testing.assert_series_equal(results, expected)


def test_calculate_contact_tracers_no_tracers():

    cases = _series_with_date_index([0.0, 1.0, 4.0])
    contact_tracers = _series_with_date_index([np.nan, np.nan, np.nan])

    results = top_level_metrics.calculate_contact_tracers(cases, contact_tracers)
    expected = _series_with_date_index([np.nan, np.nan, np.nan])
    pd.testing.assert_series_equal(results, expected)


def test_calculate_latest_rt():
    prev_rt = 1.0
    prev_rt_ci90 = 0.2
    data = _build_metrics_df(
        "36",
        dates=["2020-08-13", "2020-08-20"],
        caseDensity=[10, 10],
        testPositivityRatio=[0.1, 0.1],
        contactTracerCapacityRatio=[0.06, 0.08],
        infectionRate=[prev_rt, 2.0],
        infectionRateCI90=[prev_rt_ci90, 0.2],
    )
    metrics = top_level_metrics.calculate_latest_metrics(data, None, None)
    assert metrics.infectionRate == prev_rt
    assert metrics.infectionRateCI90 == prev_rt_ci90


def test_lookback_days():
    prev_rt = 1.0
    prev_rt_ci90 = 0.2
    data = _build_metrics_df(
        "36",
        dates=["2020-08-12", "2020-08-13", "2020-08-28"],
        caseDensity=[10, 10, None],
        testPositivityRatio=[0.1, 0.1, None],
        contactTracerCapacityRatio=[0.06, 0.6, None],
        infectionRate=[prev_rt, None, None],
        infectionRateCI90=[prev_rt_ci90, None, None],
    )

    metrics = top_level_metrics.calculate_latest_metrics(data, None, None)
    assert not metrics.caseDensity
    assert not metrics.testPositivityRatio
    assert not metrics.infectionRate
    assert not metrics.infectionRateCI90


def test_calculate_latest_rt_no_previous_row():
    data = _build_metrics_df(
        "36",
        start_date="2020-08-20",
        caseDensity=[10],
        testPositivityRatio=[0.1],
        contactTracerCapacityRatio=[0.08],
        infectionRate=[2.0],
        infectionRateCI90=[0.2],
    )
    metrics = top_level_metrics.calculate_latest_metrics(data, None, None)
    assert not metrics.infectionRate
    assert not metrics.infectionRateCI90


def test_calculate_latest_rt_no_rt():
    data = _build_metrics_df(
        "36",
        start_date="2020-08-20",
        caseDensity=[10],
        testPositivityRatio=[0.1],
        contactTracerCapacityRatio=[0.08],
    )
    metrics = top_level_metrics.calculate_latest_metrics(data, None, None)
    assert not metrics.infectionRate
    assert not metrics.infectionRateCI90


def test_calculate_latest_different_latest_days():
    prev_rt = 1.0
    prev_rt_ci90 = 0.2
    data = _build_metrics_df(
        "36",
        dates=["2020-08-13", "2020-08-20"],
        caseDensity=[10, None],
        testPositivityRatio=[0.1, 0.2],
        contactTracerCapacityRatio=[0.06, 0.08],
        infectionRate=[prev_rt, 2.01],
        infectionRateCI90=[prev_rt_ci90, 0.2],
        icuCapacityRatio=0.75,
    )
    expected_metrics = can_api_v2_definition.Metrics(
        testPositivityRatio=0.2,
        caseDensity=10,
        contactTracerCapacityRatio=0.08,
        infectionRate=prev_rt,
        infectionRateCI90=prev_rt_ci90,
        icuHeadroomRatio=None,
        icuHeadroomDetails=None,
        icuCapacityRatio=0.75,
    )
    metrics = top_level_metrics.calculate_latest_metrics(data, None, None, max_lookback_days=8)
    assert metrics == expected_metrics


def test_calculate_icu_capacity():
    region = Region.from_fips("36")
    latest = {
        CommonFields.POPULATION: 100_000,
        CommonFields.FIPS: "36",
        CommonFields.STATE: "NY",
    }
    data = _build_timeseries_dataframe(
        "36", INPUT_COLUMNS, start_date="2020-12-18", icu_beds=[10, 20], current_icu_total=[10, 15]
    )
    one_region = _fips_df_to_one_region(data, region, latest=latest)

    results, metrics = top_level_metrics.calculate_metrics_for_timeseries(
        one_region, None, None, structlog.get_logger()
    )
    expected = _build_metrics_df("36", start_date="2020-12-18", icuCapacityRatio=[1.0, 0.75],)
    positivity_method = can_api_v2_definition.TestPositivityRatioDetails(
        source=can_api_v2_definition.TestPositivityRatioMethod.OTHER
    )
    expected_metrics = top_level_metrics.calculate_latest_metrics(expected, None, positivity_method)
    pd.testing.assert_frame_equal(expected, results)
    assert metrics == expected_metrics