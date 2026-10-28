#!/usr/bin/env python3
"""Run with torchrun --standalone --nproc_per_node=2; fails on broken collectives."""
import os
from datetime import timedelta
import torch
import torch.distributed as dist

rank = int(os.environ['LOCAL_RANK'])
torch.cuda.set_device(rank)
dist.init_process_group('nccl', timeout=timedelta(seconds=120))
try:
    tensor = torch.full((1024,), rank + 1.0, device=f'cuda:{rank}')
    dist.all_reduce(tensor)
    torch.cuda.synchronize(rank)
    assert torch.all(tensor == 3).item(), 'Incorrect all-reduce result'
    print(f'Rank {rank}: NCCL all-reduce passed', flush=True)
finally:
    dist.destroy_process_group()
