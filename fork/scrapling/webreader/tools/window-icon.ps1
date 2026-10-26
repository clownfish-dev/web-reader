# Puts the red cat icon on the windows of web-reader's shared Edge (taskbar + title bar)
# and gives them their own taskbar group. -Watch keeps re-applying while that browser runs.
param([string]$Dir, [switch]$Watch)
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Collections.Generic;
public class WrIcon {
  delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr h, uint c);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, System.Text.StringBuilder s, int n);
  [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IPropertyStore { void GetCount(out uint c); void GetAt(uint i, out PKEY k); void GetValue(ref PKEY k, out PV v); void SetValue(ref PKEY k, ref PV v); void Commit(); }
  [StructLayout(LayoutKind.Sequential, Pack=4)] public struct PKEY { public Guid fmtid; public uint pid; }
  [StructLayout(LayoutKind.Explicit, Size=24)] public struct PV { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; }
  [DllImport("shell32.dll")] static extern int SHGetPropertyStoreForWindow(IntPtr h, ref Guid iid, [MarshalAs(UnmanagedType.Interface)] out IPropertyStore ps);
  static readonly Guid AUM = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
  static void SetStr(IPropertyStore ps, uint pid, string val) { var k = new PKEY { fmtid = AUM, pid = pid }; var v = new PV { vt = 31, p = Marshal.StringToCoTaskMemUni(val) }; ps.SetValue(ref k, ref v); Marshal.FreeCoTaskMem(v.p); }
  static string GetStr(IPropertyStore ps, uint pid) { var k = new PKEY { fmtid = AUM, pid = pid }; PV v; ps.GetValue(ref k, out v); return v.vt == 31 ? Marshal.PtrToStringUni(v.p) : null; }
  // Own taskbar group + icon: AppUserModel ID, relaunch command/name/icon on the window.
  public static string AppId(IntPtr h, string id, string cmd, string name, string icon) {
    var iid = typeof(IPropertyStore).GUID; IPropertyStore ps;
    if (SHGetPropertyStoreForWindow(h, ref iid, out ps) != 0) return "noprops";
    if (GetStr(ps, 3) == icon && GetStr(ps, 5) == id) return "same";
    SetStr(ps, 2, cmd); SetStr(ps, 4, name); SetStr(ps, 3, icon); SetStr(ps, 5, id);
    return "set";
  }
  public static List<IntPtr> Last = new List<IntPtr>();
  public static int Apply(uint pid, IntPtr big, IntPtr small) {
    var hs = new List<IntPtr>();
    EnumWindows((h, l) => { uint p; GetWindowThreadProcessId(h, out p);
      if (p == pid && IsWindowVisible(h) && GetWindow(h, 4) == IntPtr.Zero) { var sb = new System.Text.StringBuilder(64); GetClassName(h, sb, 64); if (sb.ToString().StartsWith("Chrome_WidgetWin")) hs.Add(h); }
      return true; }, IntPtr.Zero);
    Last = hs;
    foreach (var h in hs) {
      if (SendMessage(h, 0x7F, (IntPtr)1, IntPtr.Zero) != big) { SendMessage(h, 0x80, (IntPtr)1, big); SendMessage(h, 0x80, (IntPtr)0, small); }
    }
    return hs.Count;
  }
}
"@
$ico = Join-Path $PSScriptRoot 'web-reader-cat.ico'
$big = (New-Object System.Drawing.Icon $ico, 32, 32).Handle
$small = (New-Object System.Drawing.Icon $ico, 16, 16).Handle
$rx = '--user-data-dir="?' + [regex]::Escape($Dir) + '"?(\s|$)'
if ($Watch) {
  # one watcher per profile
  $others = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'window-icon\.ps1' -and $_.CommandLine -match '-Watch' -and $_.CommandLine -match [regex]::Escape($Dir) }
  if ($others) { exit }
}
$t0 = Get-Date
do {
  $p = Get-CimInstance Win32_Process -Filter "Name='msedge.exe' OR Name='chrome.exe'" | Where-Object { $_.CommandLine -notmatch '--type=' -and $_.CommandLine -match $rx } | Select-Object -First 1
  if (-not $p) { if ($Watch -and ((Get-Date) - $t0).TotalSeconds -lt 30) { Start-Sleep -Seconds 1; continue } else { break } }
  $n = [WrIcon]::Apply([uint32]$p.ProcessId, $big, $small)
  $cmd = '"' + $p.ExecutablePath + '" --user-data-dir="' + $Dir + '"'
  $r = foreach ($h in [WrIcon]::Last) { try { [WrIcon]::AppId($h, 'WebReader.Cat', $cmd, 'web-reader Edge', "$ico,0") } catch { 'err ' + $_.Exception.Message } }
  if (-not $Watch) { "windows $n $r" }
  Start-Sleep -Seconds 3
} while ($Watch)
