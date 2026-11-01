import assert from 'node:assert/strict';
import { compareDiscovery, emptyFundamentalFilters, fundamentalFilterError, fundamentalRequest, matchesFundamentals } from '../src/discoveryFilters.ts';

const filters={...emptyFundamentalFilters,minCap:'500',maxCap:'5000',minRoe:'10',rating:'buy_or_better'};
assert.equal(fundamentalRequest(filters).min_market_cap,500_000_000);
assert.equal(fundamentalRequest(filters).min_roe_percent,10);
assert.equal(fundamentalRequest(emptyFundamentalFilters).min_roe_percent,null);
const row={symbol:'FIXTURE.US',market_cap:1_000_000_000,roe_percent:15,analyst_rating:'buy',analyst_mean:2};
assert(matchesFundamentals(row,filters));
for(const change of [{market_cap:null},{roe_percent:null},{roe_percent:9.99},{analyst_rating:null},{analyst_rating:'hold'},{market_cap:Infinity}]) assert(!matchesFundamentals({...row,...change},filters));
assert(matchesFundamentals({symbol:'ETF.US',market_cap:null,roe_percent:null,analyst_rating:null},emptyFundamentalFilters));
assert(!matchesFundamentals({...row,roe_percent:-1},{...emptyFundamentalFilters,minRoe:'0'}));
assert(fundamentalFilterError({...filters,maxCap:'10'}));
assert(fundamentalFilterError({...filters,minRoe:'NaN'}));
assert(compareDiscovery({symbol:'A',analyst_mean:1.5},{symbol:'B',analyst_mean:2},'analyst_mean')<0);
assert(compareDiscovery({symbol:'A',analyst_mean:null},{symbol:'B',analyst_mean:2},'analyst_mean')>0);
console.log('Discovery filter checks passed: currency units, ROE thresholds, analyst categories, missing values, ranges and sort direction.');
