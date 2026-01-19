import argparse
import os
import sys

# Add the project root to the Python path to allow importing from data_source
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from data_source.hikyuu_data_source import HikyuuDataSource

DISPLAY_NAME_BLOCKLIST = [
    "QDII", "红利", "期货", "债", "港股通", "纳斯达克",  "纳指", "标普","沙特","德国","法国","香港","港股","金ETF","恒生","金股","日经","美国","东南亚","中韩"
]


def transform_code(qlib_code):
    """
    Transforms a qlib-style stock code (e.g., '159001.SZ') to hikyuu-style (e.g., 'SZ159001').
    """
    parts = qlib_code.strip().split('.')
    if len(parts) != 2:
        return None
    code, market = parts
    return f"{market.upper()}{code}"


def is_blocked(display_name):
    """
    Checks if the display name contains any of the blocked keywords.
    """
    if not display_name:
        return False
    for keyword in DISPLAY_NAME_BLOCKLIST:
        if keyword in display_name:
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description="Filter stock codes based on display name blocklist.")
    parser.add_argument("input_file", help="Path to the input text file with stock codes.")
    parser.add_argument("output_file", help="Path to the output text file.")
    args = parser.parse_args()

    if not os.path.exists(args.input_file):
        print(f"Error: Input file not found at {args.input_file}")
        return

    try:
        data_source = HikyuuDataSource()
    except Exception as e:
        print(f"Error initializing HikyuuDataSource: {e}")
        return

    print(f"Reading from {args.input_file} and writing to {args.output_file}...")
    print(f"Blocklist: {DISPLAY_NAME_BLOCKLIST}")

    blocked_count = 0
    kept_count = 0
    total_count = 0

    with open(args.input_file, 'r', encoding='utf-8') as infile, open(args.output_file, 'w', encoding='utf-8') as outfile:
        for line in infile:
            line_content = line.strip()
            if not line_content:
                continue

            total_count += 1
            parts = line_content.split()
            if not parts:
                continue
                
            original_code = parts[0]
            hikyuu_code = transform_code(original_code)

            if not hikyuu_code:
                print(f"Warning: Could not parse code from line: '{line_content}'")
                outfile.write(line)
                kept_count += 1
                continue

            try:
                info = data_source.get_security_info(hikyuu_code)
                display_name = info.display_name if info else ""

                if is_blocked(display_name):
                    print(f"Blocking {original_code} ({display_name})")
                    blocked_count += 1
                else:
                    outfile.write(line)
                    kept_count += 1
            except Exception as e:
                print(f"Error processing code {hikyuu_code}: {e}")
                # If error occurs, keep the line safely
                outfile.write(line)
                kept_count += 1

    print(f"Processing complete.")
    print(f"Total: {total_count}, Kept: {kept_count}, Blocked: {blocked_count}")


if __name__ == "__main__":
    main()
