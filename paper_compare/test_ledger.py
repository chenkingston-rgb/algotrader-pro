import copy,datetime as dt,unittest
from ledger import *
class LedgerTests(unittest.TestCase):
    def test_rule(self):
        for n in range(8):
            on={s:bool(n&(1<<i)) for i,s in enumerate(ASSETS[:3])}
            t=weights('Trend3',on);c=weights('Conditional QQQ',on)
            for a,b in zip(c,[.75*t[0],.75*t[1]+.25*on['QQQ'],.75*t[2],.75*t[3]+.25*(not on['QQQ'])]):self.assertAlmostEqual(a,b)
            self.assertAlmostEqual(sum(c),1)
    def test_ordinary_tax_exdate_sale(self):
        b=new_book();b.update(v=[100,0,0,0],cash=0);mark(b,[(.99,1,.01)]+[(1,1,0)]*3,[0,0,0,1],'ordinary30')
        self.assertAlmostEqual(b['tax'],.3);self.assertAlmostEqual(b['cash'],.7);self.assertAlmostEqual(b['nav'],99*(1-.002)+.7)
    def test_flat_and_dd(self):
        b=new_book();mark(b,[(1,1,0)]*4,[1,0,0,0],'ordinary30');self.assertAlmostEqual(b['nav'],99.9)
        mark(b,[(1,.8,0)]*4,None,'ordinary30');dd=b['dd'];mark(b,[(1,1.5,0)]*4,None,'ordinary30')
        self.assertEqual(b['dd'],0);self.assertEqual(b['worst'],dd);self.assertEqual(b['tax'],0)
    def test_bill_classification(self):
        for case,tx in [('gross',0),('ordinary30',.3),('bil_exempt',0)]:
            b=new_book();b.update(v=[0,0,0,100],cash=0);mark(b,[(1,1,0)]*3+[(.99,1,.01)],None,case);self.assertAlmostEqual(b['tax'],tx)
    def test_implied_distribution(self):
        self.assertAlmostEqual(bar_ratios((99,99,99),(100,100,99))[2],.01)
        self.assertEqual(bar_ratios((95,100,100),(100,100,100))[2],0)
        with self.assertRaises(ValueError):bar_ratios((99,99,99),(100,100,80))
    def test_launch_lock_idempotence_and_gap(self):
        days=[(dt.date(2025,8,1)+dt.timedelta(days=i)).isoformat() for i in range(550) if (dt.date(2025,8,1)+dt.timedelta(days=i)).weekday()<5]
        cal={d:('09:30','16:00') for d in days}
        bars={s:{d:(100,100,100) for d in days} for s in ASSETS}
        a=stamp("2026-11-21","08:00");l=advance(blank(),cal,bars,a)
        self.assertEqual((l['start'],l['plans'][0]['signal'],len(l['history'])),('2026-11-23','2026-10-30',0))
        state=copy.deepcopy(l);self.assertEqual(advance(l,cal,bars,a),state)
        b=stamp("2026-12-02","20:00");advance(l,cal,bars,b)
        self.assertEqual(l['plans'][-1]['kind'],'late-forward');self.assertEqual(l['plans'][-1]['open'],'2026-12-03')
        self.assertEqual(l['history'][0]['date'],'2026-11-23');self.assertAlmostEqual(l['books']['SPY']['gross']['nav'],99.9)
        x=copy.deepcopy(l);self.assertEqual(advance(l,cal,bars,b),x)
        del bars['BIL']['2026-12-03']
        with self.assertRaises(KeyError):advance(l,cal,bars,stamp("2026-12-03","20:00"))
    def test_no_orders(self):
        with self.assertRaises(ValueError):get("https://paper-api.alpaca.markets/v2/orders",{})
if __name__=='__main__':unittest.main()

