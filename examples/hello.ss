let ready = 0;
let done = 0;
let ch = 0;

def trap() {
    ch = getc();
    ready = 1;

    if (ch == 10) {
        done = 1;
    }

    iret();
}

def main() {
    print("Hello, ");

    while (done == 0) {
        while (ready == 0) {
        }

        if (done == 0) {
            putc(ch);
            ready = 0;
        }
    }

    print("!");
}
