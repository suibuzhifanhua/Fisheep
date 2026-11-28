param()
[System.Reflection.Assembly]::LoadWithPartialName("UIAutomationClient") | Out-Null
[System.Reflection.Assembly]::LoadWithPartialName("UIAutomationTypes") | Out-Null
$root = [System.Windows.Automation.AutomationElement]::RootElement
$out = New-Object System.Collections.Generic.List[string]
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ClassNameProperty, "Shell_TrayWnd")
$tray = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
if ($tray) {
  $all = $tray.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
  $out.Add("Shell_TrayWnd descendants: " + $all.Count)
  foreach ($e in $all) {
    $n = $e.Current.Name
    $c = $e.Current.ClassName
    if ($n) { $out.Add("ITEM [" + $c + "] " + $n) }
  }
} else { $out.Add("Shell_TrayWnd not found") }
$out | Set-Content -Path "C:\Users\USER\Desktop\企微伴侣\_tray2.txt" -Encoding UTF8
