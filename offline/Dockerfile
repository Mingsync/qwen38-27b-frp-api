ARG BASE_IMAGE
FROM ${BASE_IMAGE}
# Installs kernels matching this image's FlashInfer/CUDA, without a GPU build step.
RUN vllm download-kernels
