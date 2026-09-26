import akshare as ak, json
pairs=[('sz001979','2026-06-10'),('sz001979','2026-07-22'),('sz002027','2026-07-08'),
       ('sh600036','2026-06-10'),('sh600309','2026-06-10'),('sh600519','2026-05-20'),
       ('sh603288','2026-07-08'),('sh603288','2026-07-22')]
res={}
for sym,td in pairs:
    ent={}
    for adjust,tag in (('','raw'),('qfq','qfq')):
        try:
            df=ak.stock_zh_a_daily(symbol=sym,start_date=td.replace('-',''),end_date='20260925',adjust=adjust)
            import pandas as pd
            df['date']=pd.to_datetime(df['date'])
            df=df[df['date']<=td]
            ent[tag]=float(df['close'].iloc[-1]) if len(df) else None
        except Exception as e:
            ent[tag+'_err']=str(e)[:100]
    res[f'{sym[2:]}@{td}']=ent
    print(sym,td,ent)
json.dump(res,open('work/dav1312/price_cmp.json','w'))
