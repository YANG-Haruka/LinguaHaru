// LinguaHaru.app launcher (macOS, Apple Silicon).
//
// A NATIVE parent that posix_spawn()s the bundled Python as a CHILD. If the
// bundle's executable were a script that exec()s python, macOS (TCC) would
// attribute microphone / screen-recording requests to python or Terminal
// instead of LinguaHaru.app, and the permission prompt would not show up.
//
// The install root is read from Contents/Resources/root.txt, written by
// core/desktop_shortcut.py when it creates the desktop app. Output goes to
// ~/Library/Logs/LinguaHaru.log.
//
// Prebuilt into assets/macos/LinguaHaru-launcher (cross-compiled on Windows):
//   python -m ziglang cc -target aarch64-macos -O2 -o assets/macos/LinguaHaru-launcher tools/macos_launcher.c
#include <fcntl.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <spawn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

int main(void) {
    char exe[PATH_MAX], cfg[PATH_MAX], root[PATH_MAX], py[PATH_MAX], log[PATH_MAX];
    uint32_t n = sizeof exe;
    if (_NSGetExecutablePath(exe, &n) != 0) return 1;
    // .../LinguaHaru.app/Contents/MacOS/LinguaHaru -> .../LinguaHaru.app/Contents
    for (int i = 0; i < 2; i++) {
        char *slash = strrchr(exe, '/');
        if (!slash) return 1;
        *slash = 0;
    }
    snprintf(cfg, sizeof cfg, "%s/Resources/root.txt", exe);
    FILE *f = fopen(cfg, "r");
    if (!f || !fgets(root, sizeof root, f)) return 1;
    fclose(f);
    root[strcspn(root, "\r\n")] = 0;
    if (chdir(root) != 0) return 1;

    snprintf(py, sizeof py, "%s/python/bin/python3", root);
    const char *home = getenv("HOME");
    snprintf(log, sizeof log, "%s/Library/Logs/LinguaHaru.log", home ? home : "/tmp");
    setenv("PYTHONNOUSERSITE", "1", 1);   // same isolation as Start-Desktop.command
    unsetenv("PYTHONPATH");

    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_addopen(&fa, 1, log, O_WRONLY | O_CREAT | O_APPEND, 0644);
    posix_spawn_file_actions_adddup2(&fa, 1, 2);
    char *argv[] = {py, "app_qt.py", NULL};
    pid_t pid;
    if (posix_spawn(&pid, py, &fa, NULL, argv, environ) != 0) return 1;
    int st;
    waitpid(pid, &st, 0);
    return WIFEXITED(st) ? WEXITSTATUS(st) : 1;
}
