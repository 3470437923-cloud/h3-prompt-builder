param([string]$OutPath = "")
Add-Type -AssemblyName System.Drawing

$sig = @"
using System;
using System.Runtime.InteropServices;
public struct WRECT { public int L; public int T; public int R; public int B; }
public class Win {
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out WRECT r);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
}
"@
Add-Type -TypeDefinition $sig

$p = Get-Process -Name H3InputTextBuilder -ErrorAction SilentlyContinue |
     Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { Write-Output "NO WINDOW FOUND"; exit 1 }
$h = $p.MainWindowHandle

Write-Output ("pid " + $p.Id + " iconic=" + [Win]::IsIconic($h))
[Win]::ShowWindow($h, 9) | Out-Null
[Win]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 1500

$r = New-Object WRECT
[Win]::GetWindowRect($h, [ref]$r) | Out-Null
$w = $r.R - $r.L
$ht = $r.B - $r.T
Write-Output ("rect " + $r.L + "," + $r.T + " size " + $w + "x" + $ht)
if ($w -lt 200 -or $ht -lt 200) { Write-Output "WINDOW TOO SMALL"; exit 1 }

$bmp = New-Object System.Drawing.Bitmap $w, $ht
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.Clear([System.Drawing.Color]::White)
$hdc = $g.GetHdc()
$ok = [Win]::PrintWindow($h, $hdc, 3)
$g.ReleaseHdc($hdc)
$g.Dispose()
Write-Output ("PrintWindow ok=" + $ok)

if ($OutPath -eq "") { $OutPath = Join-Path $PSScriptRoot "preview.png" }
$bmp.Save($OutPath, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Output ("saved " + $OutPath + " " + (Get-Item $OutPath).Length + " bytes")
