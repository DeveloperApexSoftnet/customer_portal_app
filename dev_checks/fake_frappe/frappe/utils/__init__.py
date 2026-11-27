import datetime, html
def nowdate(): return "2026-11-27"
def getdate(v): return v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v)[:10])
def add_days(d, n): return getdate(d) + datetime.timedelta(days=n)
def flt(v): return float(v or 0)
def cint(v): return int(v or 0)
escape_html = html.escape
def strip_html(s): return s
