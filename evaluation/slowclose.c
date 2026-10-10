/* slowclose: make close() on one file take a little longer (Linux, LD_PRELOAD).
**
** Used by evaluation/contention.py and by nothing else. It is never part of
** the application or of its container image.
**
** A process that closes any descriptor of a file loses every POSIX lock it
** holds on that file. SQLite therefore decides, before it closes a database
** file, whether another connection of the process holds a lock, and postpones
** the close if one does. The decision and the close() are two steps. A lock
** that another thread takes between them is released by the close() without
** SQLite noticing (defect D-7, docs/test-report.md).
**
** Between the two steps lie a few machine instructions, so the fault shows
** itself once in about a hundred thousand requests. This library waits before
** each close() of the file named by SLOWCLOSE_SUFFIX, for SLOWCLOSE_USEC
** microseconds. The system call itself is unchanged; only its timing is, as
** on a slower or busier machine. Software that is correct must give the same
** results with the delay as without it.
**
**     cc -O2 -shared -fPIC -o slowclose.so slowclose.c -ldl
**     LD_PRELOAD=./slowclose.so SLOWCLOSE_SUFFIX=/phishaware.db SLOWCLOSE_USEC=300 python ...
*/
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int (*real_close)(int) = 0;

int close(int fd){
  const char *suffix = getenv("SLOWCLOSE_SUFFIX");
  if( !real_close ) real_close = (int (*)(int))dlsym(RTLD_NEXT, "close");
  if( suffix && suffix[0] ){
    char link[64], path[4096];
    int saved = errno;
    ssize_t n;
    snprintf(link, sizeof link, "/proc/self/fd/%d", fd);
    n = readlink(link, path, sizeof path - 1);
    if( n>0 ){
      size_t length = strlen(suffix);
      path[n] = 0;
      if( (size_t)n>=length && strcmp(path + n - length, suffix)==0 ){
        const char *usec = getenv("SLOWCLOSE_USEC");
        usleep(usec ? (useconds_t)atoi(usec) : 300);
      }
    }
    errno = saved;
  }
  return real_close(fd);
}
