# Tesseract OCR models (tessdata_fast)

The three `.traineddata` files in this directory are third-party models, not Synwevo code. They are
byte-for-byte copies of the files in **tesseract-ocr/tessdata_fast**
(<https://github.com/tesseract-ocr/tessdata_fast>), licensed under the **Apache License 2.0**; the
license text is in [`LICENSE`](LICENSE). They are unmodified, and upstream ships no NOTICE file.

| File | Upstream commit that last changed it | Git blob (same upstream and here) | Bytes | SHA-256 |
|---|---|---|---|---|
| `chi_sim.traineddata` | `80d92b7db61cb6ddc519049990be34e4c913566f` (2018-02-20) | `388bac276d033d06e5ed5ba7a7ad14ae58f97dab` | 2 469 156 | `a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730` |
| `eng.traineddata` | `923915d4ced2a7235221788285785a29c4a42d4a` (2017-09-14) | `bbef4675053b5b468cdb477053e28b1c698ba08e` | 4 113 088 | `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2` |
| `osd.traineddata` | `139ff127aaee3cb0270fd29411fec75d610d728c` (2017-09-15) | `527457ca8f8fe1fda7c2f88bce3c0e4be12be9d0` | 10 562 727 | `9cf5d576fcc47564f11265841e5ca839001e7e6f38ff7f7aacf46d15a96b00ff` |

**Where they are used.** `synwevo_docs/engine/ocr.py` falls back to this directory when
`SYNWEVO_DOCS_TESSDATA` is unset, which is the local development setup. The container image
(`infra/Dockerfile`) installs Debian's `tesseract-ocr-chi-sim` and `tesseract-ocr-eng` packages and
points `SYNWEVO_DOCS_TESSDATA` at them, so production does not read these files.

**Source offer.** The `/api/source-code` archive leaves the `.traineddata` files out: they are
unmodified third-party data, available from the upstream repository above. This README and the
LICENSE next to them are in the archive.

## 中文

本目录的三个 `.traineddata` 是第三方 OCR 模型，不是 Synwevo 的代码：与 tesseract-ocr/tessdata_fast
中的文件逐字节一致，以 Apache-2.0 许可（全文见 `LICENSE`），未作修改。本地开发在未设置
`SYNWEVO_DOCS_TESSDATA` 时使用它们；容器镜像改用 Debian 的 tesseract 语言包。`/api/source-code`
的源代码包不含这些模型文件（第三方数据，可从上游获取），只含本说明与 LICENSE。
