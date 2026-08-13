#include "arrow/status.h"
#include "arrow/util/fuzz_internal.h"
#include "parquet/arrow/reader.h"

extern "C" int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size) {
  auto status = parquet::arrow::internal::FuzzReader(data, static_cast<int64_t>(size));
  arrow::internal::LogFuzzStatus(status, data, static_cast<int64_t>(size));
  return 0;
}
