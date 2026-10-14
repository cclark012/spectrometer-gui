import pytest
from devices.lakeshore_335 import LakeShore335, EmulatedLakeShore335


class Transport:
    def __init__(self,responses): self.responses=iter(responses); self.commands=[]; self.closed=False
    def write(self,data): self.commands.append(data)
    def read_until(self,*args): return next(self.responses)
    def close(self): self.closed=True


def test_read_only_queries_and_status():
    t=Transport([b'LSCI,MODEL335,123,1.0\r\n',b'4.2\r\n',b'0\r\n',b'0\r\n',b'32\r\n'])
    a=LakeShore335('ignored',transport=t)
    r=a.read()
    assert r[0].value == 4.2 and r[0].source == 'lakeshore_A'
    assert r[1].status == 32 and not r[1].fresh(r[1].sampled_s,5.)
    assert t.commands == [b'*IDN?\r\n',b'KRDG? A\r\n',b'RDGST? A\r\n',b'KRDG? B\r\n',b'RDGST? B\r\n']
    with pytest.raises(ValueError): a._query('SETP 1,10')
    a.close(); assert t.closed


def test_wrong_device_and_timeout_close_port():
    for response in [b'OTHER\r\n',b'partial']:
        t=Transport([response])
        with pytest.raises((ValueError,TimeoutError)): LakeShore335('x',transport=t)
        assert t.closed


def test_emulator_identity_and_values():
    r=EmulatedLakeShore335().read()
    assert [p.value for p in r] == [4.2,4.3]
    assert all('EMULATED' in p.identity for p in r)
