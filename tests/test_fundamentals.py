import json
import math
import sqlite3
import unittest
from baseline.fundamentals import FEATURES,feature_values,normalize,financial_peers

class Fundamentals(unittest.TestCase):
    def facts(self):
        return {('Assets','20221231',0):{100},('Liabilities','20221231',0):{50},
          ('PropertyPlantAndEquipmentNet','20221231',0):{20},('Revenues','20221231',4):{50},
          ('Revenues','20211231',4):{40},('NetIncomeLoss','20221231',4):{5}}

    def test_definitions_and_same_period_prior_revenue(self):
        p,error=feature_values(self.facts(),'20221231')
        self.assertIsNone(error)
        self.assertAlmostEqual(p['features']['log_assets'],math.log(100))
        self.assertEqual(p['features']['net_margin'],.1)
        self.assertEqual(p['features']['revenue_growth'],.25)
        self.assertEqual(p['features']['liabilities_assets'],.5)
        self.assertEqual(p['features']['ppe_assets'],.2)

    def test_missing_or_conflicting_is_not_imputed(self):
        facts=self.facts();facts[('Assets','20221231',0)]={100,200}
        self.assertIsNone(feature_values(facts,'20221231')[0])
        facts=self.facts();del facts['Revenues','20211231',4]
        self.assertIsNone(feature_values(facts,'20221231')[0])
        facts=self.facts();facts['Revenues','20211231',4]={0}
        self.assertIsNone(feature_values(facts,'20221231')[0])

    def test_combined_ppe_and_summed_liabilities_are_traced(self):
        f=self.facts();del f['Liabilities','20221231',0];f['LiabilitiesCurrent','20221231',0]={20};f['LiabilitiesNoncurrent','20221231',0]={30}
        del f['PropertyPlantAndEquipmentNet','20221231',0]
        tag='PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization'
        f[tag,'20221231',0]={25}
        p,error=feature_values(f,'20221231')
        self.assertIsNone(error);self.assertEqual(p['features']['ppe_assets'],.25)
        self.assertEqual(p['tags']['net_ppe'],tag)
        self.assertIn('+',p['tags']['liabilities'])

    def test_scaling_and_neighbours_exclude_self_and_other_years(self):
        records={i:dict(features={key:float(i*j) for j,(key,_) in enumerate(FEATURES,1)}) for i in (1,2,100)}
        vectors,params=normalize(records)
        for key,_ in FEATURES:self.assertAlmostEqual(sum(v[key] for v in vectors.values()),0)
        with sqlite3.connect(':memory:') as d:
            d.executescript('''CREATE TABLE financial_profiles(year INTEGER,gvkey INTEGER,profile TEXT,vector TEXT);
              CREATE TABLE financial_exclusions(year INTEGER,gvkey INTEGER,reason TEXT);
              CREATE TABLE financial_models(year INTEGER,parameters TEXT);
              CREATE TABLE identities(gvkey INTEGER,name TEXT,ticker TEXT,cik TEXT);''')
            d.execute('INSERT INTO financial_models VALUES (2023,?)',(json.dumps(params),))
            for year,g,value in [(2023,1,0),(2023,2,1),(2023,3,-1),(2024,4,0)]:
                d.execute('INSERT INTO financial_profiles VALUES (?,?,?,?)',(year,g,'{}',json.dumps({key:value for key,_ in FEATURES})))
                d.execute('INSERT INTO identities VALUES (?,?,?,?)',(g,'Company '+str(g),'X',str(g)))
            p=financial_peers(d,2023,1)
            self.assertEqual([c['gvkey'] for c in p['peers']],[2,3])
            self.assertAlmostEqual(p['peers'][0]['distance'],math.sqrt(5))
            self.assertFalse(financial_peers(d,2023,999)['available'])
