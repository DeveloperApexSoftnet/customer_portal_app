import types, datetime
class _D(dict):
    __getattr__ = dict.get
local = types.SimpleNamespace(response={}, session=types.SimpleNamespace(user="buyer@x", sid="s", data={}), form_dict={}, message_log=[])
session = local.session
conf = {}
DB = {}          # name -> dict (SO header)
DELETED = []
SAVED = []
COMMENTS = []
def whitelist(**k): return lambda f: f
def set_user(u): local.session.user = u
def get_roles(u): return ["Customer"]
def parse_json(s):
    import json; return json.loads(s)
def log_error(**k): pass
def clear_messages(): pass
def get_traceback(): return ""
def get_all(dt, filters=None, pluck=None): return ["CUST-A"]
class _DB:
    def get_value(self, dt, name, fields=None, as_dict=False, for_update=False):
        if dt == "User": return "Website User"
        if dt == "Customer": return None if fields == "disabled" else None
        if dt == "Company": return "INR"
        if dt == "Price List": return "INR"
        if dt == "Sales Order":
            r = DB.get(name)
            if r is None: return None
            return _D(r) if as_dict else r.get(fields)
    def sql(self, q, v=None, as_dict=False):
        if "Dynamic Link" in q: return [("CUST-A", "CONT-1")]
        if "distinct company" in q: return [("CO",)]
        if "tabItem" in q:
            return [_D(item_code=c, uom="Nos", default_warehouse="WH", rate=10.0) for c in v["codes"] if c != "BAD"]
        return []
    def rollback(self, **k): pass
db = _DB()
class _Doc(_D):
    def __init__(self, d): super().__init__(d); self.__dict__ = self; self.flags = types.SimpleNamespace()
    def set(self, k, v): self[k] = v
    def append(self, k, v): self.setdefault(k, []).append(v)
    def save(self): SAVED.append(dict(self)); DB[self["name"]].update(modified="2026-11-27 13:00:00")
    def add_comment(self, t, c): COMMENTS.append(c)
def get_doc(dt, name=None):
    r = DB[name]; d = _Doc(dict(r)); d.items = [1]; return d
def delete_doc(dt, name, ignore_permissions=False): DELETED.append(name); DB.pop(name)
