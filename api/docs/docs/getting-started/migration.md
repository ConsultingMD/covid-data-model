---
id: migration
title: Migrating to v2
---


Lots has changed in the COVID data landscape since we create the first version of our API.
Our API largely was focused around interventions taken at the state level and the API structure reflected that.

Additionally, the data reported and collected has evolved. When we first launched our API few states were reporting
hospitalizations and even fewer reporting test data. 

:::note

The existing API will be maintained for some time, but we will stop updating at some point in the future.

:::

### Interventions are no longer included at the route level
We no longer surface projections by intervention.

Here are examples of API endpoint changes:
```diff
- https://data.covidactnow.org/latest/state/{state}.{intervention}.json
+ https://data.covidactnow.org/v2/latest/state/{state}.json
```
```diff
- https://data.covidactnow.org/latest/county/{fips}.{intervention}.json
+ https://data.covidactnow.org/v2/latest/county/{fips}.json
```
```diff
- https://data.covidactnow.org/latest/counties.{intervention}.json
+ https://data.covidactnow.org/v2/latest/counties.json
```
```diff
- https://data.covidactnow.org/latest/states.{intervention}.json
+ https://data.covidactnow.org/v2/latest/states.json
```

### Projections deprecated

The projections and projections timeseries are not included in the new API.
If you still need access to our intervention projection models, please use the [V1 API](https://github.com/covid-projections/covid-data-model/blob/master/api/README.V1.md). 

As our projection models evolve, we are planning on adding projections back in, but they may look
different than the old format.


### Top level metrics included

We are now surfacing the top level metrics that power the CAN risk levels.
These are available under `metrics` and `metricsTimeseries` keys.

### Fields renamed

Many fields have been slightly renamed for clarity.  Double check the [API Reference](/api) for details.