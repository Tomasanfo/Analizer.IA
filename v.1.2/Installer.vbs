' Installer.vbs - Lanzador 100% silencioso de Analizer IA local  (by @tomasanfo)
' Abre installer_gui.py con pyw/pythonw (sin ventana de CMD).
' Nota: este archivo es ASCII a proposito (Windows Script Host no lee UTF-8).
Option Explicit

Dim fso, shell, here
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = here

' Ejecuta un comando oculto (ventana 0) y espera. True si termino con codigo 0.
Function Works(cmd)
    Dim rc
    On Error Resume Next
    rc = shell.Run("cmd /c " & cmd & " >nul 2>&1", 0, True)
    If Err.Number <> 0 Then
        Works = False
        Err.Clear
    Else
        Works = (rc = 0)
    End If
    On Error GoTo 0
End Function

Dim launcher
launcher = ""

' 1) Python Launcher con version recomendada (tkinter viene incluido)
If launcher = "" Then
    If Works("py -3.12 -c ""import tkinter""") Then launcher = "pyw -3.12"
End If
If launcher = "" Then
    If Works("py -3.11 -c ""import tkinter""") Then launcher = "pyw -3.11"
End If
If launcher = "" Then
    If Works("py -3.10 -c ""import tkinter""") Then launcher = "pyw -3.10"
End If
' 2) Cualquier Python 3 del launcher
If launcher = "" Then
    If Works("py -3 -c ""import tkinter""") Then launcher = "pyw -3"
End If
' 3) python / pythonw del PATH
If launcher = "" Then
    If Works("python -c ""import tkinter""") Then launcher = "pythonw"
End If

If launcher = "" Then
    MsgBox "No encontre Python en tu PC (o le falta tkinter)." & vbCrLf & vbCrLf & _
           "Instala Python 3.12 desde python.org, marca la casilla " & _
           """Add Python to PATH"" y vuelve a hacer doble clic en Installer.vbs." & vbCrLf & vbCrLf & _
           "Te abro la pagina de descarga ahora.", vbCritical, "Analizer IA local - Instalador"
    shell.Run "https://www.python.org/downloads/windows/"
    WScript.Quit 1
End If

' Ventana oculta (0) y sin esperar: la unica ventana visible es la grafica del instalador.
shell.Run launcher & " """ & here & "\installer_gui.py""", 0, False
