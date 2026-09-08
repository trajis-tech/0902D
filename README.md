# Python Registration v31 — 真正單檔可攜設定版

v31 以 v30 為基礎，保留既有功能：

- 四個核准解析式 ideal stadium
- 產品級 affine registration
- 每顆 R 的 X/Y 位置最佳化
- R1/R2、R3/R4 最短距離 FAIL
- 四 R 相對 dx/dy 離散 WARNING
- 單張與批量上傳完全分流
- 批量 XLSX 與本地 API
- 單張僅保留一張結果顯影圖
- PyInstaller `--onefile` 建置腳本

v31 保留 v30 的統一資料根目錄，並修正單檔可攜設定：EXE 內嵌打包當下的 `algorithm_config.json` 作為原廠預設。首次執行會自動建立 **`D:\XrayRegistrationData\algorithm_config.json`**；若該檔已存在則直接使用，不會覆寫。

## 最重要的使用方式

1. 打開 `D:\XrayRegistrationData\algorithm_config.json`。
2. 修改需要的值。
3. 儲存。
4. 關閉程式／EXE。
5. 重新啟動。

設定檔在程序啟動時載入一次；執行中不會熱更新，避免同一批資料前後使用不同參數。

如果 JSON 格式錯誤、缺少必要欄位、數值超出允許範圍，程式會 fail-fast 並回報設定錯誤，不會默默使用寫死的舊值。

## 設定檔位置

原始專案：

```text
Python Registration/
  algorithm_config.json   <- 唯一可調設定來源
  app/
  tests/
  portable_python/
```

PyInstaller 建置後：

```text
dist/
  XrayRegistration_v31.exe
  （不需要任何 JSON sidecar；預設 JSON 已內嵌 EXE）
```

因此 EXE 是真正 one-file：第一次執行由 EXE 內建預設值產生可編輯 JSON；後續只需修改 D 槽 JSON 並重啟。

## v31 執行期資料位置

預設只在 D 槽建立一個根資料夾：

```text
D:\XrayRegistrationData\
  results\          <- 單張結果圖、批量結果圖與 XLSX
  temp\             <- latest_inspection.png 等暫存
  uploads\
    single\         <- 單張上傳
    batch\          <- 批量上傳
```

EXE 所在資料夾不再產生 `output`、`temp`、`workspace`。若要換磁碟或資料夾，只需修改：

```json
"runtime": {
  "dataRoot": "D:\\XrayRegistrationData"
}
```

儲存後重新啟動程式／EXE 即生效。指定磁碟不存在或無寫入權限時，程式會明確報錯，不會偷偷改存其他位置。

## 主要判定參數

`userParameters` 為目前介面也能做單次覆寫的公開參數；JSON 中的值是啟動預設值。

| key | 預設值 | 用途 |
|---|---:|---|
| `rect_mu_pct` | -8.42 | 單高斯 μ 相對 G |
| `rect_sigma2` | 25 | 單高斯變異數 |
| `min_area_ratio` | 0.05 | 最小產品矩形面積比例 |
| `rect_bin_pct` | -4.16 | 找框二值門檻相對 G |
| `solder_reference_ceiling_pct` | -46.22 | 焊錫基準候選上限 |
| `solder_center_offset_pct` | 12.79 | 焊錫／空缺過渡中心；降低通常會提高少錫敏感度 |
| `solder_transition_pct` | 9.58 | 焊錫到空缺的完整灰階過渡寬度 |
| `ideal_shift_max_px` | 30 | 每顆 R 的 X/Y 最大搜尋距離 |
| `pair_min_distance_px` | 10 | 同排兩 R 最小距離；低於即 FAIL |
| `relative_shift_warning_px` | 10 | 四 R dx/dy 離散警告值 |
| `crop_ratio` | 0.5 | 中央量測裁剪比例 |

## 其他已集中設定

同一 `algorithm_config.json` 內還包含：

- `productDetection`
  - G 上下帶比例
  - 找框開運算 kernel 比例／最小值
  - 全畫面誤框排除比例
  - 外部灰階量測的 dilation kernel
- `orientation`
  - A/B 最低 ECC 分數與最低分差
  - 工作解析度
  - percentile normalization
  - Gaussian sigma
  - ECC iteration / epsilon / filter size
- `registration`
  - product affine ECC 分數
  - 工作解析度與 iteration
  - percentile normalization
  - mask 擴張比例與有效面積下限
  - affine scale / translation 限制
  - stadium polygon 採樣數與幾何驗證門檻
- `solderReference`
  - 最低候選像素數／比例
  - histogram sigma
  - peak 搜尋半徑
  - 最低 peak 比例
- `positionOptimization`
  - X/Y 搜尋步進
- `idealGeometry`
  - R1～R4 的 center / axis / straightHalfLength / radius
- `rasterization`
  - supersampling 與 mask threshold
- `resultRender`
  - 顯影 alpha、顏色、輪廓線及資訊面板外觀
- `runtime`
  - `dataRoot`：執行期資料根目錄，預設 `D:\XrayRegistrationData`
  - host / port
  - 是否自動開瀏覽器
  - 單張／批量大小限制
  - 單批最多張數
  - JSON request 上限

詳細欄位請看 `ALGORITHM_CONFIG_REFERENCE.md`。

## 正式計算流程

```text
原始 X-ray
  -> 由上下灰階帶計算 G
  -> 找封裝矩形、旋正、判定 A/B 方向
  -> 非焊錫產品結構做 product-level affine registration
  -> 將 4 顆 ideal stadium 映射至中央 crop
  -> 由未平移 ideal ROI 固定焊錫灰階基準 S
  -> 建立 frozen solder-weight map
  -> 每顆沿 X/Y 搜尋使加權空焊率最小
  -> R1/R2、R3/R4 最短距離 QA
  -> 四 R dx/dy 相對離散 QA
  -> PASS / WARNING / FAIL + 單張結果圖
```

### 焊錫／少錫核心

以 `S` 為焊錫灰階基準：

```text
center = S + G * solder_center_offset_pct / 100
width  = G * solder_transition_pct / 100
T_solder = center - width/2
T_void   = center + width/2
```

權重：

```text
weight = clip((T_void - gray) / (T_void - T_solder), 0, 1)
voidRate = 1 - sum(weight inside ROI) / ROI pixels
```

如果某些圖的少錫區在 ROI 內但面積抓得偏少，通常先降低 `solder_center_offset_pct`，例如由 `12.79` 逐步試 `11.5`、`10.5`；建議用良品／NG 樣本 DOE 後再定版。

## 單張與批量

單張：

- `D:\XrayRegistrationData\uploads\single`（預設）
- `POST /api/upload-image` 欄位：`file`
- `POST /api/process`

批量：

- `D:\XrayRegistrationData\uploads\batch`（預設）
- `POST /api/v1/batch` 欄位：`files`
- 回應直接為 XLSX

兩條路徑仍維持 v28 的強制分流。

## 啟動

```text
點此開始.bat
```

原始碼模式讀取專案根目錄 `algorithm_config.json`；打包後 EXE 則讀取 `D:\XrayRegistrationData\algorithm_config.json`，不存在時由 EXE 內建預設自動建立。

## 一鍵 EXE

```text
一鍵打包單檔EXE.bat
```

腳本會：

1. 驗證依賴。
2. 驗證 `algorithm_config.json`。
3. 建立 `dist\XrayRegistration_v31.exe`。
4. 將 `algorithm_config.json` 內嵌進單檔 EXE，不再在 `dist` 產生 JSON sidecar。

打包腳本仍維持 ASCII + CRLF，避免 Windows CMD 對中文路徑／LF 的解析問題。
