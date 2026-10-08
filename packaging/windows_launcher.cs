using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class Program
{
    [STAThread]
    static void Main(string[] args)
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string app = Path.Combine(dir, "eProContactAudit-app.exe");
        string dll = Path.Combine(dir, "_internal", "python314.dll");
        bool ready = File.Exists(app) && File.Exists(dll);
        if (args.Length > 0 && args[0] == "--check")
        {
            Console.WriteLine(ready ? "ready" : "missing");
            return;
        }
        if (ready)
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = app,
                WorkingDirectory = dir,
                UseShellExecute = false,
            });
            return;
        }

        MessageBox.Show(
            "Windows opened this app from inside the zip, so Python and the rest of the files were left behind.\r\n\r\n" +
            "Close this message, then:\r\n" +
            "1. Right-click eProContactAudit-windows.zip\r\n" +
            "2. Click Extract All, then Extract\r\n" +
            "3. Open that folder and double-click eProContactAudit.exe\r\n\r\n" +
            "Do not double-click the app while you are still looking inside the zip.",
            "ePro Contact Audit Agent",
            MessageBoxButtons.OK,
            MessageBoxIcon.Warning);
    }
}
