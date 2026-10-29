param([string]$Exe = "", [string]$Out = "")
# Dump the icon resource that Windows actually associates with the exe, so a Python/PIL
# pass can compare it against app.ico under identical rules.
# ASCII only on purpose: inline Chinese gets mangled by PowerShell 5.1 -Command.
Add-Type -AssemblyName System.Drawing
$root = $PSScriptRoot
if ($Exe -eq "") { $Exe = Join-Path $root "dist\H3InputTextBuilder.exe" }
if ($Out -eq "") { $Out = Join-Path $root "_exe_icon.png" }
if (-not (Test-Path $Exe)) { Write-Output "FAIL: exe missing"; exit 1 }

$ex = [System.Drawing.Icon]::ExtractAssociatedIcon($Exe)
if ($null -eq $ex) { Write-Output "FAIL: no icon resource found in exe"; exit 1 }
$bmp = $ex.ToBitmap()
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
Write-Output ("exe icon " + $bmp.Width + "x" + $bmp.Height + " -> " + $Out)
$bmp.Dispose(); $ex.Dispose()
exit 0
