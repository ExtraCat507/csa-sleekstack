let done = 0;
let ch = 0;

def trap() {
    ch = getc();

    if (ch == 10) {
        done = 1;
    } else {
        putc(ch);
    }

    iret();
}

def main() {
    while (done == 0) {
    }
}
