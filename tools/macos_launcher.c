// LinguaHaru.app launcher (macOS, Apple Silicon).
//
// A NATIVE parent that posix_spawn()s the bundled Python as a CHILD. If the
// bundle's executable were a script that exec()s python, macOS (TCC) would
// attribute microphone / screen-recording requests to python or Terminal
// instead of LinguaHaru.app, and the permission prompt would not show up.
//
// The install root is read from Contents/Resources/root.txt (written by
// core/desktop_shortcut.py for the Desktop copy). Without it — the
// LinguaHaru.app shipped inside the download — the root is the folder that
// holds the .app, and only if that folder really is an install (app_qt.py +
// python/bin/python3). Only the bundled python/ gets its download quarantine
// cleared. Output goes to ~/Library/Logs/LinguaHaru.log.
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
    if (f && fgets(root, sizeof root, f)) {
        root[strcspn(root, "\r\n")] = 0;
    } else {                        // .../X/LinguaHaru.app/Contents -> .../X
        for (int i = 0; i < 2; i++) {
            char *slash = strrchr(exe, '/');
            if (!slash) return 1;
            *slash = 0;
        }
        snprintf(root, sizeof root, "%s", exe);
    }
    if (f) fclose(f);
    if (chdir(root) != 0) return 1;

    // Only run a real LinguaHaru install: the .app moved on its own (e.g. into
    // ~/Downloads) must not execute whatever "python" sits next to it.
    char probe[PATH_MAX], pydir[PATH_MAX];
    snprintf(probe, sizeof probe, "%s/app_qt.py", root);
    snprintf(py, sizeof py, "%s/python/bin/python3", root);
    if (access(probe, R_OK) != 0 || access(py, X_OK) != 0) return 1;

    // Clear the download quarantine from the bundled Python ONLY (never the
    // whole folder) so it may run; no-op when there is none.
    snprintf(pydir, sizeof pydir, "%s/python", root);
    pid_t xp;
    char *xargv[] = {"/usr/bin/xattr", "-dr", "com.apple.quarantine", pydir, NULL};
    if (posix_spawn(&xp, xargv[0], NULL, NULL, xargv, environ) == 0) {
        int xs;
        waitpid(xp, &xs, 0);
    }

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
