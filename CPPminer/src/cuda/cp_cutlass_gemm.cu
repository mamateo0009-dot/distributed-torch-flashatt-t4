#include "cp_cutlass.h"
#include "cp_config.h"

#include "cp_cutlass_gemm_types.h"
#include "cp_cutlass_layout.h"

#include <cuda_runtime.h>
#include <stdio.h>

#define CP_CUTLASS_CHECK(status)                                               \
  do {                                                                         \
    cutlass::Status _st = (status);                                            \
    if (_st != cutlass::Status::kSuccess) {                                    \
      fprintf(stderr, "[cutlass] %s:%d status %d\n", __FILE__, __LINE__,       \
              static_cast<int>(_st));                                          \
      return -1;                                                               \
    }                                                                          \
  } while (0)

static cp_cutlass::FusedMilestoneGemmOp<cp_cutlass::Gemm128x128StepMajor>
    g_fused_step_major[MAX_GPUS];
static cp_cutlass::FusedMilestoneGemmOp<cp_cutlass::Gemm128x128RowMajor>
    g_fused_row_major[MAX_GPUS];

static cp_cutlass::FusedMilestoneGemmOp<cp_cutlass::Gemm128x128StepMajorTensorOp>
    g_fused_step_major_tensorop[MAX_GPUS];
static cp_cutlass::FusedMilestoneGemmOp<cp_cutlass::Gemm128x128RowMajorTensorOp>
    g_fused_row_major_tensorop[MAX_GPUS];

static cp_cutlass::FusedMilestoneGemmOp<cp_cutlass::Gemm128x128StepMajorSm80TensorOp>
    g_fused_step_major_sm80_tensorop[MAX_GPUS];
static cp_cutlass::FusedMilestoneGemmOp<cp_cutlass::Gemm128x128RowMajorSm80TensorOp>
    g_fused_row_major_sm80_tensorop[MAX_GPUS];

static int g_configured_attributes[MAX_GPUS] = {0};

static void cp_cutlass_configure_attributes(int dev)
{
  const int dev_idx = (dev >= 0 && dev < MAX_GPUS) ? dev : 0;
  if (g_configured_attributes[dev_idx]) return;
  cudaSetDevice(dev);
  const void* simt_ptr = (const void*)cutlass::Kernel<typename cp_cutlass::Gemm128x128RowMajor::GemmKernel>;
  cudaFuncSetAttribute(simt_ptr, cudaFuncAttributePreferredSharedMemoryCarveout, cudaSharedmemCarveoutMaxShared);

  const void* tensorop_ptr = (const void*)cutlass::Kernel<typename cp_cutlass::Gemm128x128RowMajorTensorOp::GemmKernel>;
  cudaFuncSetAttribute(tensorop_ptr, cudaFuncAttributePreferredSharedMemoryCarveout, cudaSharedmemCarveoutMaxShared);

  const void* sm80_ptr = (const void*)cutlass::Kernel<typename cp_cutlass::Gemm128x128RowMajorSm80TensorOp::GemmKernel>;
  cudaFuncSetAttribute(sm80_ptr, cudaFuncAttributePreferredSharedMemoryCarveout, cudaSharedmemCarveoutMaxShared);
  g_configured_attributes[dev_idx] = 1;
}

int cp_cutlass_device_ok(int dev)
{
  cudaDeviceProp prop;
  if (cudaGetDeviceProperties(&prop, dev) != cudaSuccess) {
    return 0;
  }
  /* Supports Pascal (sm_61), Volta (sm_70), Turing (sm_75), Ampere (sm_80/86), Ada (sm_89), and newer. */
  return prop.major >= 6;
}

int cp_cutlass_is_tensorop_supported(int dev)
{
  cudaDeviceProp prop;
  if (cudaGetDeviceProperties(&prop, dev) != cudaSuccess) {
    return 0;
  }
  if (prop.major == 7 && prop.minor == 5) return 1; // Turing (sm_75)
  if (prop.major >= 8) return 2; // Ampere (sm_80, sm_86), Ada (sm_89), Hopper (sm_90), Blackwell (sm_100, sm_120)
  return 0;
}

static int cp_cutlass_get_arch_mode(int dev)
{
  return cp_cutlass_is_tensorop_supported(dev);
}

size_t cp_cutlass_tiles_per_batch(int row_batch_count, int col_batch_count)
{
  return static_cast<size_t>(row_batch_count) *
         static_cast<size_t>(col_batch_count) *
         static_cast<size_t>(MmaLaneTile128x128::kThreadsPerCta);
}

size_t cp_cutlass_tile_xor_bytes(int row_batch_count, int col_batch_count)
{
  const int num_steps = K_DIM / R_RANK;
  return cp_cutlass_tiles_per_batch(row_batch_count, col_batch_count) *
         static_cast<size_t>(num_steps) * sizeof(uint32_t);
}

int cp_cutlass_period_batch(
    int dev, const int8_t* d_Ap, const int8_t* d_BpT, int m, int n,
    int row_period0, int col_period0, int row_batch_count, int col_batch_count,
    int step_major, uint32_t* d_tile_xor, size_t tiles_per_batch,
    const CpCutlassJackpotLaunch* jackpot, cudaStream_t stream)
{
  if (cudaSetDevice(dev) != cudaSuccess) {
    return -1;
  }

  const int M = row_batch_count * CP_CUTLASS_CTA_M;
  const int N_fat = col_batch_count * CP_CUTLASS_CTA_N;
  const int K = K_DIM;
  const int cta_cols = col_batch_count;
  const size_t tile_count = tiles_per_batch;

  const int8_t* d_A = d_Ap;
  const int8_t* d_B = d_BpT;
  if (step_major) {
    d_A = d_Ap + (size_t)row_period0 * CP_CUTLASS_CTA_M * R_RANK;
    d_B = d_BpT + (size_t)col_period0 * CP_CUTLASS_CTA_N * R_RANK;
  } else {
    d_A = d_Ap + (size_t)row_period0 * CP_CUTLASS_CTA_M * K_DIM;
    d_B = d_BpT + (size_t)col_period0 * CP_CUTLASS_CTA_N * K_DIM;
  }

  cutlass::Status st = cutlass::Status::kErrorInternal;
  cp_cutlass_configure_attributes(dev);

  const int arch_mode = cp_cutlass_get_arch_mode(dev);
  const int dev_idx = (dev >= 0 && dev < MAX_GPUS) ? dev : 0;

  if (arch_mode == 2) {
    // Ada Lovelace (sm_89) / Ampere (sm_80, sm_86) TensorOp m16n8k32
    if (step_major) {
      CP_CUTLASS_CHECK(g_fused_step_major_sm80_tensorop[dev_idx].initialize(
          M, N_fat, K, m, n, const_cast<int8_t*>(d_A), const_cast<int8_t*>(d_B),
          d_tile_xor, cta_cols, tile_count, jackpot));
      st = g_fused_step_major_sm80_tensorop[dev_idx](stream);
    } else {
      CP_CUTLASS_CHECK(g_fused_row_major_sm80_tensorop[dev_idx].initialize(
          M, N_fat, K, m, n, const_cast<int8_t*>(d_A), const_cast<int8_t*>(d_B),
          d_tile_xor, cta_cols, tile_count, jackpot));
      st = g_fused_row_major_sm80_tensorop[dev_idx](stream);
    }
  } else if (arch_mode == 1) {
    // Turing (sm_75) TensorOp m8n8k16
    if (step_major) {
      CP_CUTLASS_CHECK(g_fused_step_major_tensorop[dev_idx].initialize(
          M, N_fat, K, m, n, const_cast<int8_t*>(d_A), const_cast<int8_t*>(d_B),
          d_tile_xor, cta_cols, tile_count, jackpot));
      st = g_fused_step_major_tensorop[dev_idx](stream);
    } else {
      CP_CUTLASS_CHECK(g_fused_row_major_tensorop[dev_idx].initialize(
          M, N_fat, K, m, n, const_cast<int8_t*>(d_A), const_cast<int8_t*>(d_B),
          d_tile_xor, cta_cols, tile_count, jackpot));
      st = g_fused_row_major_tensorop[dev_idx](stream);
    }
  } else {
    // Pascal (sm_61) / Volta SIMT DP4A
    if (step_major) {
      CP_CUTLASS_CHECK(g_fused_step_major[dev_idx].initialize(
          M, N_fat, K, m, n, const_cast<int8_t*>(d_A), const_cast<int8_t*>(d_B),
          d_tile_xor, cta_cols, tile_count, jackpot));
      st = g_fused_step_major[dev_idx](stream);
    } else {
      CP_CUTLASS_CHECK(g_fused_row_major[dev_idx].initialize(
          M, N_fat, K, m, n, const_cast<int8_t*>(d_A), const_cast<int8_t*>(d_B),
          d_tile_xor, cta_cols, tile_count, jackpot));
      st = g_fused_row_major[dev_idx](stream);
    }
  }
  if (st != cutlass::Status::kSuccess) {
    fprintf(stderr, "[cutlass] kernel launch failed status %d\n",
            static_cast<int>(st));
    return -1;
  }
  return 0;
}
