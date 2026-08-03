param(
  [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$outputDir = Join-Path $ProjectRoot "static\img\pwa"
$sourceLogoPath = Join-Path $ProjectRoot "static\img\logo1.png"
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

function New-RoundedPath {
  param(
    [System.Drawing.RectangleF]$Rect,
    [float]$Radius
  )

  $path = [System.Drawing.Drawing2D.GraphicsPath]::new()
  $diameter = [Math]::Max(1.0, $Radius * 2.0)
  $path.AddArc($Rect.X, $Rect.Y, $diameter, $diameter, 180, 90)
  $path.AddArc($Rect.Right - $diameter, $Rect.Y, $diameter, $diameter, 270, 90)
  $path.AddArc($Rect.Right - $diameter, $Rect.Bottom - $diameter, $diameter, $diameter, 0, 90)
  $path.AddArc($Rect.X, $Rect.Bottom - $diameter, $diameter, $diameter, 90, 90)
  $path.CloseFigure()
  return $path
}

function Draw-BrandMark {
  param(
    [System.Drawing.Graphics]$Graphics,
    [System.Drawing.RectangleF]$Rect,
    [switch]$RoundedBackground
  )

  $start = [System.Drawing.PointF]::new($Rect.X, $Rect.Y)
  $end = [System.Drawing.PointF]::new($Rect.Right, $Rect.Bottom)
  $gradient = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
    $start,
    $end,
    [System.Drawing.ColorTranslator]::FromHtml("#15915B"),
    [System.Drawing.ColorTranslator]::FromHtml("#06442B")
  )

  try {
    if ($RoundedBackground) {
      $backgroundPath = New-RoundedPath -Rect $Rect -Radius ($Rect.Width * 0.28)
      try { $Graphics.FillPath($gradient, $backgroundPath) }
      finally { $backgroundPath.Dispose() }
    } else {
      $Graphics.FillRectangle($gradient, $Rect)
    }
  } finally {
    $gradient.Dispose()
  }

  $scale = $Rect.Width / 64.0
  $map = {
    param([float]$X, [float]$Y)
    [System.Drawing.PointF]::new($Rect.X + ($X * $scale), $Rect.Y + ($Y * $scale))
  }

  $document = [System.Drawing.Drawing2D.GraphicsPath]::new()
  $document.StartFigure()
  $document.AddLine((& $map 20 15.5), (& $map 39.5 15.5))
  $document.AddLine((& $map 39.5 15.5), (& $map 48 24))
  $document.AddLine((& $map 48 24), (& $map 48 48.5))
  $document.AddLine((& $map 48 48.5), (& $map 20 48.5))
  $document.CloseFigure()

  $whitePen = [System.Drawing.Pen]::new([System.Drawing.Color]::White, [Math]::Max(2.0, 3.2 * $scale))
  $whitePen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
  $whitePen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
  $whitePen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
  try {
    $Graphics.DrawPath($whitePen, $document)
    $Graphics.DrawLine($whitePen, (& $map 39.5 15.5), (& $map 39.5 24))
    $Graphics.DrawLine($whitePen, (& $map 39.5 24), (& $map 48 24))
  } finally {
    $whitePen.Dispose()
    $document.Dispose()
  }

  $goldPen = [System.Drawing.Pen]::new(
    [System.Drawing.ColorTranslator]::FromHtml("#E8C98C"),
    [Math]::Max(2.0, 4.0 * $scale)
  )
  $goldPen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
  $goldPen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
  $goldPen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
  try {
    $Graphics.DrawLines($goldPen, [System.Drawing.PointF[]]@(
      (& $map 25.5 36),
      (& $map 30.2 40.8),
      (& $map 39.8 30.6)
    ))
  } finally {
    $goldPen.Dispose()
  }
}

function New-PwaIcon {
  param(
    [int]$Size,
    [ValidateSet("any", "maskable", "apple")][string]$Kind,
    [string]$FileName
  )

  $bitmap = [System.Drawing.Bitmap]::new($Size, $Size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  try {
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality

    if ($Kind -eq "any") {
      $graphics.Clear([System.Drawing.ColorTranslator]::FromHtml("#F3F7F4"))
      $padding = [float]($Size * 0.07)
      $rect = [System.Drawing.RectangleF]::new($padding, $padding, $Size - (2 * $padding), $Size - (2 * $padding))
      Draw-BrandMark -Graphics $graphics -Rect $rect -RoundedBackground
    } else {
      $rect = [System.Drawing.RectangleF]::new(0, 0, $Size, $Size)
      Draw-BrandMark -Graphics $graphics -Rect $rect
    }

    $target = Join-Path $outputDir $FileName
    $bitmap.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
  } finally {
    $graphics.Dispose()
    $bitmap.Dispose()
  }
}

function New-StartupImage {
  param(
    [int]$Width,
    [int]$Height,
    [string]$FileName,
    [System.Drawing.Image]$SourceLogo
  )

  $bitmap = [System.Drawing.Bitmap]::new($Width, $Height, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  try {
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality

    $background = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
      [System.Drawing.PointF]::new(0, 0),
      [System.Drawing.PointF]::new($Width, $Height),
      [System.Drawing.ColorTranslator]::FromHtml("#F8FBF9"),
      [System.Drawing.ColorTranslator]::FromHtml("#E4F0E9")
    )
    try { $graphics.FillRectangle($background, 0, 0, $Width, $Height) }
    finally { $background.Dispose() }

    $greenGlow = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(22, 0, 108, 53))
    $goldGlow = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(22, 185, 151, 91))
    try {
      $glowSize = [float]([Math]::Min($Width, $Height) * 0.72)
      $graphics.FillEllipse($greenGlow, -($glowSize * 0.48), -($glowSize * 0.42), $glowSize, $glowSize)
      $graphics.FillEllipse($goldGlow, $Width - ($glowSize * 0.55), $Height - ($glowSize * 0.48), $glowSize, $glowSize)
    } finally {
      $greenGlow.Dispose()
      $goldGlow.Dispose()
    }

    $isLandscape = $Width -gt $Height
    $shortEdge = [float][Math]::Min($Width, $Height)
    $markSize = [float]($shortEdge * $(if ($isLandscape) { 0.28 } else { 0.25 }))

    if ($isLandscape) {
      $markRect = [System.Drawing.RectangleF]::new($Width * 0.22 - $markSize / 2, ($Height - $markSize) / 2, $markSize, $markSize)
      $logoWidth = [float]($Width * 0.42)
      $logoHeight = [float]($logoWidth * 199.0 / 417.0)
      $logoRect = [System.Drawing.RectangleF]::new($Width * 0.68 - $logoWidth / 2, ($Height - $logoHeight) / 2, $logoWidth, $logoHeight)
    } else {
      $markRect = [System.Drawing.RectangleF]::new(($Width - $markSize) / 2, $Height * 0.34 - $markSize / 2, $markSize, $markSize)
      $logoWidth = [float]($Width * 0.62)
      $logoHeight = [float]($logoWidth * 199.0 / 417.0)
      $logoRect = [System.Drawing.RectangleF]::new(($Width - $logoWidth) / 2, $markRect.Bottom + ($markSize * 0.18), $logoWidth, $logoHeight)
    }

    $shadowRect = [System.Drawing.RectangleF]::new($markRect.X + ($markSize * 0.025), $markRect.Y + ($markSize * 0.04), $markRect.Width, $markRect.Height)
    $shadowPath = New-RoundedPath -Rect $shadowRect -Radius ($markSize * 0.28)
    $shadowBrush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(28, 3, 30, 18))
    try { $graphics.FillPath($shadowBrush, $shadowPath) }
    finally { $shadowBrush.Dispose(); $shadowPath.Dispose() }

    Draw-BrandMark -Graphics $graphics -Rect $markRect -RoundedBackground

    $sourceRect = [System.Drawing.RectangleF]::new(46, 165, 417, 199)
    $graphics.DrawImage($SourceLogo, $logoRect, $sourceRect, [System.Drawing.GraphicsUnit]::Pixel)

    $lineWidth = [float]([Math]::Min($logoRect.Width * 0.26, $shortEdge * 0.16))
    $lineY = [float]($logoRect.Bottom + ($shortEdge * 0.035))
    $linePen = [System.Drawing.Pen]::new([System.Drawing.ColorTranslator]::FromHtml("#B9975B"), [Math]::Max(3.0, $shortEdge * 0.006))
    $linePen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $linePen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    try { $graphics.DrawLine($linePen, ($Width - $lineWidth) / 2, $lineY, ($Width + $lineWidth) / 2, $lineY) }
    finally { $linePen.Dispose() }

    $target = Join-Path $outputDir $FileName
    $bitmap.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
  } finally {
    $graphics.Dispose()
    $bitmap.Dispose()
  }
}

New-PwaIcon -Size 192 -Kind any -FileName "icon-192.png"
New-PwaIcon -Size 512 -Kind any -FileName "icon-512.png"
New-PwaIcon -Size 192 -Kind maskable -FileName "icon-maskable-192.png"
New-PwaIcon -Size 512 -Kind maskable -FileName "icon-maskable-512.png"
New-PwaIcon -Size 180 -Kind apple -FileName "apple-touch-icon-180.png"

$profiles = @(
  @{ Name = "iphone-375x812-3x"; Width = 1125; Height = 2436 },
  @{ Name = "iphone-390x844-3x"; Width = 1170; Height = 2532 },
  @{ Name = "iphone-393x852-3x"; Width = 1179; Height = 2556 },
  @{ Name = "iphone-402x874-3x"; Width = 1206; Height = 2622 },
  @{ Name = "iphone-414x896-2x"; Width = 828; Height = 1792 },
  @{ Name = "iphone-414x896-3x"; Width = 1242; Height = 2688 },
  @{ Name = "iphone-430x932-3x"; Width = 1290; Height = 2796 },
  @{ Name = "iphone-440x956-3x"; Width = 1320; Height = 2868 },
  @{ Name = "ipad-768x1024-2x"; Width = 1536; Height = 2048 },
  @{ Name = "ipad-820x1180-2x"; Width = 1640; Height = 2360 },
  @{ Name = "ipad-834x1194-2x"; Width = 1668; Height = 2388 },
  @{ Name = "ipad-1024x1366-2x"; Width = 2048; Height = 2732 }
)

$sourceLogo = [System.Drawing.Image]::FromFile($sourceLogoPath)
try {
  foreach ($profile in $profiles) {
    New-StartupImage -Width $profile.Width -Height $profile.Height -FileName ("splash-{0}-portrait.png" -f $profile.Name) -SourceLogo $sourceLogo
    New-StartupImage -Width $profile.Height -Height $profile.Width -FileName ("splash-{0}-landscape.png" -f $profile.Name) -SourceLogo $sourceLogo
  }
} finally {
  $sourceLogo.Dispose()
}

Write-Output "Generated PWA assets in $outputDir"
