#!/bin/bash
#SBATCH --job-name=stale-file-sweeper
#SBATCH --output=stale-sweeper-%j.out
#SBATCH --error=stale-sweeper-%j.err
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --time=04:00:00
#SBATCH --mem=8G

module load python/3.10

python3 touch_stale_files.py \
    --paths /gpfs/ordinary/home/user /gpfs/shared/project \
    --days 365 \
    --workers 16 \
    --ignore-dir custom_scratch backup_old \
    --log-file stale_sweep_run.log