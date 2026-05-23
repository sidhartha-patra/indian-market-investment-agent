from src.agent.recommend import generate_recommendations

r = generate_recommendations()
print("\n=== Volatile movers (top 10) ===")
for x in r["volatile_movers"][:10]:
    print(f"  {x['ticker']:18} score={x['score']:3} {x['signal']:10} "
          f"dir={x['direction']:5} px={x['last_price']:>9} vol%={x['ann_vol_pct']:>5} "
          f"atr%={x['atr_pct']:>4} ret5d={x['return_5d_pct']:>6}%")
print("\n=== Top picks (momentum x quality) ===")
for x in r["top_picks"][:5]:
    print(f"  {x['ticker']:18} score={x['score']:3} {x['signal']:6} px={x['last_price']:>9}")
ns = r["news_summary"]
print(f"\nNews: bullish={ns['bullish']} bearish={ns['bearish']} neutral={ns['neutral']}")
