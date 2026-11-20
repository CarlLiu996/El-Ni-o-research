"""Synthetic PSL grids; baseline and version must never be silently combined."""
import pytest
from agri_cta.sources import noaa_psl


def sample(version='ERSST V6'):
    return ('2000 2000\n2000 -1 0 1 2 3 4 5 6 7 -99.99 -99.99 -99.99\n-99.99\nNINA34\n5N-5S 170W-120W\n'+version+'\nAnomaly from 1981-2010\nunits=degC\n').encode()


def test_grid_sentinel_and_distinct_dataset_key():
    a=noaa_psl(sample(),'ersstv6');b=noaa_psl(sample('HadISST'),'hadisst')
    assert len(a)==9 and a[-1]['observation_date']=='2000-09-30'
    assert a[0]['value']==-1 and a[0]['metric_id']!=b[0]['metric_id']


@pytest.mark.parametrize('body', [sample('ERSST V5'),sample().replace(b'1981-2010',b'1991-2020'),sample().replace(b'5N-5S',b'10N-10S'),sample().replace(b'-99.99\nNINA34',b'-999\nNINA34'),sample().replace(b'2000 -1',b'2001 -1')])
def test_reject_incompatible_identity_or_grid(body):
    with pytest.raises(ValueError):noaa_psl(body,'ersstv6')
