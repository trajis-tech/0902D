# algorithm_config.json 欄位說明

> EXE 首次執行會由內建預設建立 `D:\XrayRegistrationData\algorithm_config.json`；之後修改該檔並重新啟動 EXE 即可。百分比欄位中 `12.79` 代表 12.79%，不是 0.1279。

## userParameters

這些是主要製程／判定參數，也是 UI 目前可做單次覆寫的欄位。JSON 值為程式啟動時的預設。

- `rect_mu_pct`：單高斯 μ 相對 G 的百分比。
- `rect_sigma2`：單高斯 variance。
- `min_area_ratio`：產品框最小影像面積比例。
- `rect_bin_pct`：找框 THRESH_BINARY 相對 G 的百分比。
- `solder_reference_ceiling_pct`：估計 S 時可進入暗色候選的灰階上限，相對 G。
- `solder_center_offset_pct`：S 到顯影中心的 G 百分比偏移。
- `solder_transition_pct`：確定焊錫至確定空缺的完整過渡寬度，以 G 百分比表示。
- `ideal_shift_max_px`：每顆 ideal stadium X/Y 共用最大平移搜尋距離。
- `pair_min_distance_px`：R1-R2 或 R3-R4 最小輪廓距離；低於即 FAIL。
- `relative_shift_warning_px`：四 R 的 dx 或 dy spread 超過此值即 WARNING。
- `crop_ratio`：中央 crop 對產品框寬高比例。

## productDetection

- `backgroundStripFraction`：計算 G 時，上／下各取影像高度的比例。
- `openKernelRatio`：找框黑色 mask 開運算 kernel 相對短邊比例。
- `openKernelMinPx`：開運算 kernel 最小像素。
- `fullFrameRejectAreaRatio`：候選矩形面積超過全圖此比例視為誤抓整張。
- `fullFrameRejectWidthRatio` / `fullFrameRejectHeightRatio`：候選寬高幾乎佔滿全圖時排除。
- `outsideDilateKernelPx`：計算產品矩形外部平均 A 前，對矩形 mask dilation 的 kernel。

## orientation

- `minScore`：A/B 方向最佳 ECC 最低分數。
- `minMargin`：A/B ECC 最低分差。
- `maxWidth`：方向比對工作影像最大寬度。
- `featureMinWidth` / `featureMinHeight`：方向特徵最小工作尺寸。
- `featureSigma`：方向特徵 Gaussian sigma。
- `normalizePercentileLow` / `normalizePercentileHigh`：robust normalization percentile。
- `normalizeMinRange`：灰階範圍太小時視為無有效特徵的下限。
- `eccMaxIterations` / `eccEpsilon` / `eccGaussianFilterSize`：方向 ECC 收斂參數。

## registration

- `productPoseMinScore`：產品級 affine ECC 最低放行分數。
- `productPoseMaxWidth`：產品級配準工作影像最大寬度。
- `productPoseMinWorkWidth` / `productPoseMinWorkHeight`：最低工作尺寸。
- `productPoseMaxIterations`：最大 ECC iteration。
- `eccMinIterations`：即使 max iterations 設得很小，仍至少執行的 iteration。
- `normalizePercentileLow` / `normalizePercentileHigh` / `normalizeMinRange`：產品級 robust normalization。
- `featureSigma`：產品級特徵 Gaussian sigma。
- `eccEpsilon` / `eccGaussianFilterSize`：affine ECC 收斂參數。
- `maskRadiusScale`：配準時遮蔽焊錫區的 stadium radius 倍率。
- `maskStraightExtensionRadiusScale`：直線半長額外延伸的 radius 倍率。
- `maskBorderRatio` / `maskMinBorderPx`：產品外框邊界遮蔽寬度。
- `maskMinValidFraction`：剩餘可用配準區域最低比例。
- `maxTranslationRatio`：affine translation 相對影像最大邊長上限。
- `minScale` / `maxScale`：affine linear singular value 範圍。
- `capSamples`：解析式 stadium 兩端圓弧採樣密度。
- `idealMinContourPoints`：理想輪廓最低點數。
- `idealMinAreaPixels`：理想 ROI rasterize 後最低像素面積。
- `contourSupportStride`：輪廓支撐度診斷取樣步距。

## solderReference

- `minSeedPixels`：S 候選至少像素數。
- `minSeedFraction`：S 候選至少佔理想 ROI 比例。
- `histogramSigmaMinGray`：histogram Gaussian sigma 最低灰階值。
- `histogramSigmaRatioOfG`：histogram sigma 對 G 的比例。
- `peakRadiusSigmaMultiplier`：主峰附近取樣半徑 = sigma × 此值。
- `minPeakFraction`：主峰取樣像素至少佔 seed 的比例。

## positionOptimization

- `stepPx`：X/Y 搜尋步進。現行最佳化實作以整數 pixel translation 為主，建議維持 1。

## idealGeometry

四顆 stadium 的核准幾何全部集中在這裡：

- `center`: `[x, y]`
- `axis`: stadium 長軸單位方向
- `straightHalfLength`: 直線段半長
- `radius`: 端帽半徑

這些參數會直接改變理論量測 ROI，修改前應有量測治具／標準樣本依據。

## rasterization

- `supersample`：輪廓 rasterization 放大倍率。
- `maskThreshold`：縮回原尺寸後形成二值 mask 的門檻。
- `overlayLineThicknessSupersampled`：supersampled overlay 線寬。

## resultRender

只影響結果圖顯示，不改變空焊計算：

- 背景 dim factor
- void overlay 最大 alpha / BGR 顏色
- contour 顏色／線寬
- PASS/WARNING/FAIL 顏色
- 文字顏色、字級、行高
- 資訊面板大小與暗化比例

## runtime

- `dataRoot`：所有執行期產生資料的統一根目錄。v31 預設 `D:\XrayRegistrationData`。
- `host` / `port`
- `autoOpenBrowser`
- `maxSingleUploadMb`
- `maxBatchUploadMb`
- `maxBatchFiles`
- `maxJsonMb`
- `defaultOutputListLimit`

這些設定同樣在啟動時讀取，因此改完需重啟。
