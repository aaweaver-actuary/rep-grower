#!/usr/bin/env python3

import itertools
import subprocess

def make_query(digits):
    return f"""
attach 'ducklake:lichessan' as lichess;

create schema if not exists lichess.raw;

create or replace view raw."{digits}" as (
with raw as (
  from read_json('~/Downloads/lichess_db_eval.jsonl.zst')
),

hashed as (
  select
    sha256(replace(fen, ' ', '')) as fen_hash,
    *

  from raw
),

first_two as (
  select
    substr(fen_hash, 1, 1) as first1,
    substr(fen_hash, 2, 1) as second1,
    substr(fen_hash, 1, 2) as first2,
    *

  from hashed

)

select
  fen_hash,
  fen,
  evals
from first_two
where first2='{digits}'
)
;"""

HEX_DIGITS = "0123456789abcdef"

if __name__ == "__main__":
    for (d1, d2) in itertools.product(HEX_DIGITS, repeat=2):
        digits = f"{d1}{d2}"
        query = make_query(digits)

        subprocess.run(
            ["duckdb", "~/.lichess-analysis/", "-c", query],
            check=True
        )
