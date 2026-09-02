# PrintWindow (PW_RENDERFULLCONTENT) screenshot by window title + owner PID; no foreground needed
# -Close: close all windows matching title (cleanup of leftovers from previous runs)
param(
  [string]$Title,
  [string]$Out,
  [int]$OwnerPid = 0,
  [switch]$Close
)

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
using System.Collections.Generic;
public class WinCap {
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr w, IntPtr l);
  /* 进程默认 DPI-unaware 时 GetWindowRect 返回虚拟化（缩小）坐标，而 PrintWindow
     按物理像素渲染 WebView2 —— 位图只有逻辑宽，右/下被裁。须先声明 DPI aware。 */
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  public const uint WM_CLOSE = 0x0010;
  public static List<IntPtr> Find(string needle, uint pid) {
    var list = new List<IntPtr>();
    EnumWindows((h, l) => {
      if (!IsWindowVisible(h)) return true;
      var sb = new StringBuilder(512);
      GetWindowText(h, sb, 512);
      if (!sb.ToString().Contains(needle)) return true;
      uint p; GetWindowThreadProcessId(h, out p);
      if (pid != 0 && p != pid) return true;
      list.Add(h);
      return true;
    }, IntPtr.Zero);
    return list;
  }
}
"@

[void][WinCap]::SetProcessDPIAware()

if ($Close) {
  $wins = [WinCap]::Find($Title, 0)
  foreach ($h in $wins) { [void][WinCap]::PostMessage($h, [WinCap]::WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero) }
  Write-Output ("closed " + $wins.Count)
  exit 0
}

$hwnd = [WinCap]::Find($Title, [uint32]$OwnerPid) | Select-Object -First 1
if (-not $hwnd) { Write-Error "window not found: $Title (pid $OwnerPid)"; exit 1 }

$r = New-Object WinCap+RECT
[void][WinCap]::GetWindowRect($hwnd, [ref]$r)
$w = $r.Right - $r.Left; $h = $r.Bottom - $r.Top
if ($w -le 0 -or $h -le 0) { Write-Error "bad rect"; exit 1 }

$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$dc = $g.GetHdc()
[void][WinCap]::PrintWindow($hwnd, $dc, 2)
$g.ReleaseHdc($dc)
$g.Dispose()
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
Write-Output "saved: $Out ($w x $h)"
