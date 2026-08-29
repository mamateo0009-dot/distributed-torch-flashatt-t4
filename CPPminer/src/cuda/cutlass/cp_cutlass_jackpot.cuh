/* In-kernel jackpot finalize for CUTLASS Case 9 fused period GEMM. */
#pragma once

#include "cp_config.h"
#include "cp_gpu.cuh"
#include "mma_lane_tile.h"

#define CP_CUTLASS_JACKPOT_WORDS 16
#define CP_CUTLASS_JACKPOT_LROT 13

using CutlassJackpotTile = MmaLaneTile128x128;
using CutlassTensorOpJackpotTile = MmaTensorOpLaneTile128x128;

__device__ __forceinline__ uint32_t cp_cutlass_rotl32(uint32_t x, int s)
{
#if defined(__CUDA_ARCH__)
    return __funnelshift_l(x, x, s);
#else
    return (x << s) | (x >> (32 - s));
#endif
}

__device__ __forceinline__ uint32_t cp_cutlass_lop3_xor3(uint32_t a, uint32_t b, uint32_t c)
{
#if defined(__CUDA_ARCH__)
    uint32_t d;
    asm("lop3.b32 %0, %1, %2, %3, 0x96;" : "=r"(d) : "r"(a), "r"(b), "r"(c));
    return d;
#else
    return a ^ b ^ c;
#endif
}

/* 64-element balanced lop3.b32 3-input XOR reduction tree */
template <typename AccumArrayT>
__device__ __forceinline__ uint32_t cp_cutlass_reduce_accum64_lop3(const AccumArrayT& accum)
{
    const uint32_t* a = reinterpret_cast<const uint32_t*>(accum.data());

    // Level 1: 64 inputs -> 21 trios (63 elements) + 1 residual = 22 words
    uint32_t t1[22];
    #pragma unroll
    for (int i = 0; i < 21; ++i) {
        t1[i] = cp_cutlass_lop3_xor3(a[3 * i], a[3 * i + 1], a[3 * i + 2]);
    }
    t1[21] = a[63];

    // Level 2: 22 words -> 7 trios (21 elements) + 1 residual = 8 words
    uint32_t t2[8];
    #pragma unroll
    for (int i = 0; i < 7; ++i) {
        t2[i] = cp_cutlass_lop3_xor3(t1[3 * i], t1[3 * i + 1], t1[3 * i + 2]);
    }
    t2[7] = t1[21];

    // Level 3: 8 words -> 2 trios (6 elements) + 2 residuals = 4 words
    uint32_t t3[4];
    t3[0] = cp_cutlass_lop3_xor3(t2[0], t2[1], t2[2]);
    t3[1] = cp_cutlass_lop3_xor3(t2[3], t2[4], t2[5]);
    t3[2] = t2[6];
    t3[3] = t2[7];

    // Level 4: 4 words -> 1 trio + 1 residual = 2 words
    uint32_t t4 = cp_cutlass_lop3_xor3(t3[0], t3[1], t3[2]);
    return t4 ^ t3[3];
}

__device__ __forceinline__ void cp_cutlass_jackpot_fold_step(
    uint32_t* jackpot_words, int step, uint32_t partial_xor)
{
    const int tid = step % CP_CUTLASS_JACKPOT_WORDS;
    jackpot_words[tid] =
        cp_cutlass_rotl32(jackpot_words[tid], CP_CUTLASS_JACKPOT_LROT) ^ partial_xor;
}

__device__ __forceinline__ bool cp_cutlass_jackpot_target_ok(
    const uint32_t digest[8], const uint32_t bound[8])
{
    for(int w = 7; w >= 0; w--){
        if(digest[w] < bound[w]) return true;
        if(digest[w] > bound[w]) return false;
    }
    return true;
}

template <typename TileType = CutlassJackpotTile>
__device__ __forceinline__ void cp_cutlass_tile_origin(
    int row_period, int col_period, int thread_idx,
    int* out_t_rows, int* out_t_cols)
{
    const int cta_row0 = row_period * CP_CUTLASS_CTA_M;
    const int cta_col0 = col_period * CP_CUTLASS_CTA_N;
    int row = 0;
    int col = 0;
    TileType::thread_cell_global(
        cta_row0, cta_col0, thread_idx, row, col);
    *out_t_rows = row;
    *out_t_cols = col;
}

template <typename TileType = CutlassJackpotTile>
__device__ __forceinline__ void cp_cutlass_jackpot_try(
    const uint32_t jackpot_words[CP_CUTLASS_JACKPOT_WORDS],
    const uint32_t* a_key8,
    const uint32_t bound[8],
    int row_period,
    int col_period,
    int thread_idx,
    int* found_flag,
    int* out_t_rows,
    int* out_t_cols)
{
    uint32_t msg[CP_CUTLASS_JACKPOT_WORDS];
    for(int i = 0; i < CP_CUTLASS_JACKPOT_WORDS; i++)
        msg[i] = jackpot_words[i];

    uint32_t digest[8];
    b3_compress64(a_key8, msg, digest);

    if(!cp_cutlass_jackpot_target_ok(digest, bound))
        return;

    if(atomicCAS(found_flag, 0, 1) != 0)
        return;

    int t_rows = 0;
    int t_cols = 0;
    cp_cutlass_tile_origin<TileType>(row_period, col_period, thread_idx,
                                     &t_rows, &t_cols);
    *out_t_rows = t_rows;
    *out_t_cols = t_cols;
}
