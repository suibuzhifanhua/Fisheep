param()
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ClassNameProperty, "Shell_TrayWnd")
$tray = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
$out = New-Object System.Collections.Generic.List[string]
if ($tray) {
  $all = $tray.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
  foreach ($e in $all) {
    $n = $e.Current.Name
    if ($n -and ($n -match "伴侣|企微|WeCom")) { $out.Add("TRAY-ICON: " + $n) }
  }
  $out.Add("(tray elements scanned: " + $all.Count + ")")
} else {
  $out.Add("Shell_TrayWnd not found")
}
# 同时枚举溢出区（隐藏图标窗）
$cond2 = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ClassNameProperty, "TopLevelWindowForOverflowXamlIsland")
$ov = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $cond2)
foreach ($t in $ov) {
  $all2 = $t.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
  foreach ($e in $all2) {
    $n = $e.Current.Name
    if ($n -and ($n -match "伴侣|企微|WeCom")) { $out.Add("OVERFLOW-ICON: " + $n) }
  }
  $out.Add("(overflow window scanned: " + $all2.Count + " elements)")
}
$out | Set-Content -Path "C:\Users\USER\Desktop\企微伴侣\_tray.txt" -Encoding UTF8
