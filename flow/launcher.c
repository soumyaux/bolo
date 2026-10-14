// Bolo.app's executable, compiled by flow/login.py with ROOT and LOG set.
// It starts Python as a child instead of becoming Python: macOS then asks whether Bolo.app has
// a permission, not whether Python does. A shell script here would make macOS check Python.
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
static pid_t child;

static void forward(int sig) {
    if (child > 0) kill(child, sig);
}

int main(void) {
    if (chdir(ROOT) != 0) return 1;
    int log = open(LOG, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (log >= 0) {
        dup2(log, 1);
        dup2(log, 2);
    }
    char *args[] = {ROOT "/.venv/bin/python", "-m", "flow.app", NULL};
    // Logging out or quitting from outside stops Python too.
    signal(SIGTERM, forward);
    signal(SIGINT, forward);
    signal(SIGHUP, forward);
    if (posix_spawn(&child, args[0], NULL, NULL, args, environ) != 0) return 1;
    int status = 0;
    while (waitpid(child, &status, 0) < 0 && errno == EINTR) {
    }
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
