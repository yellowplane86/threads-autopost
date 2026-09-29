# 投稿用の文字情報カード（1080x1350 PNG）を作る。写真は使わない＝経路⑤（自作）。
# 使い方: powershell -File tools/make_cards.ps1
Add-Type -AssemblyName System.Drawing
$ErrorActionPreference = 'Stop'
$W = 1080; $H = 1350; $M = 84

function C($hex, $a = 255) { $c = [System.Drawing.ColorTranslator]::FromHtml($hex); [System.Drawing.Color]::FromArgb($a, $c) }
function F($name, $size) { New-Object System.Drawing.Font($name, $size, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel) }

function Draw-Card($card, $outPath) {
    $bmp = New-Object System.Drawing.Bitmap($W, $H)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = 'AntiAlias'; $g.TextRenderingHint = 'AntiAliasGridFit'
    $rect = New-Object System.Drawing.Rectangle(0, 0, $W, $H)
    $bg = New-Object System.Drawing.Drawing2D.LinearGradientBrush($rect, (C $card.bg1), (C $card.bg2), 65)
    $g.FillRectangle($bg, $rect)

    # 装飾：大きな円（淡）
    $deco = New-Object System.Drawing.SolidBrush((C $card.accent 22))
    $g.FillEllipse($deco, 620, -220, 720, 720)
    $g.FillEllipse($deco, -260, 980, 560, 560)
    if ($card.stars) {
        $rnd = New-Object System.Random(29)
        for ($i = 0; $i -lt 70; $i++) {
            $s = $rnd.Next(2, 5); $b = New-Object System.Drawing.SolidBrush((C '#ffffff' ($rnd.Next(60, 200))))
            $g.FillEllipse($b, $rnd.Next(0, $W), $rnd.Next(0, 420), $s, $s)
        }
    }

    $white = New-Object System.Drawing.SolidBrush((C '#ffffff'))
    $muted = New-Object System.Drawing.SolidBrush((C '#ffffff' 170))
    $accent = New-Object System.Drawing.SolidBrush((C $card.accent))
    $right = New-Object System.Drawing.StringFormat; $right.Alignment = 'Far'

    # PR表記は画像に入れない（返信にだけ入れる）

    # キッカー
    $g.FillRectangle($accent, $M, 84, 10, 34)
    $g.DrawString($card.kicker, (F 'Noto Sans JP Medium' 34), $white, $M + 26, 72)

    # 見出し
    $y = 170
    $hf = F 'Noto Sans JP Black' $card.hsize
    for ($i = 0; $i -lt $card.head.Count; $i++) {
        $brush = if ($i -eq $card.head.Count - 1) { $accent } else { $white }
        $g.DrawString($card.head[$i], $hf, $brush, $M - 8, $y)
        $y += [int]($card.hsize * 1.3)
    }
    $y += 40

    # チップ
    if ($card.chips) {
        $cf = F 'Noto Sans JP Black' 40; $x = $M
        foreach ($t in $card.chips) {
            $sz = $g.MeasureString($t, $cf)
            $pen = New-Object System.Drawing.Pen((C $card.accent), 3)
            $g.DrawRectangle($pen, $x, $y, [int]$sz.Width + 22, 76)
            $g.DrawString($t, $cf, $white, $x + 11, $y + 10)
            $x += [int]$sz.Width + 40
        }
        $y += 120
    }

    # 行
    $lf = F 'Noto Sans JP Medium' 30; $vf = F 'Noto Sans JP Black' $card.vsize; $rf = F 'Noto Sans JP Medium' 30
    $line = New-Object System.Drawing.Pen((C '#ffffff' 70), 2)
    foreach ($r in $card.rows) {
        $g.DrawLine($line, $M, $y, $W - $M, $y)
        $g.DrawString($r[0], $lf, $muted, $M, $y + 18)
        $g.DrawString($r[1], $vf, $white, $M - 4, $y + 58)
        if ($r[2]) { $g.DrawString($r[2], $rf, $accent, (New-Object System.Drawing.RectangleF(0, ($y + 18), ($W - $M), 50)), $right) }
        $y += $card.rowh
    }
    $g.DrawLine($line, $M, $y, $W - $M, $y)

    # 締めの一文
    $g.DrawString($card.bottom, (F 'Noto Sans JP Black' 44), $white, $M - 4, $y + 44)

    # 出典
    $g.DrawString($card.footer, (F 'Noto Sans JP Medium' 24), $muted, $M, $H - 84)

    $g.Dispose()
    $bmp.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
}

$root = Split-Path -Parent $PSScriptRoot
$cardsFile = Join-Path $PSScriptRoot 'cards.json'
$data = Get-Content $cardsFile -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($day in $data.PSObject.Properties) {
    $dir = Join-Path $root "images/$($day.Name)"
    New-Item -ItemType Directory -Force $dir | Out-Null
    foreach ($slot in $day.Value.PSObject.Properties) {
        $out = Join-Path $dir "$($slot.Name).png"
        Draw-Card $slot.Value $out
        "wrote $out"
    }
}
