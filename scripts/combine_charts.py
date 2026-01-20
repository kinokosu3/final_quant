import os
import argparse
import math
import matplotlib.pyplot as plt
import matplotlib.image as mpimg

def combine_charts(fa_path):
    """
    Combine images with the same name from multiple subdirectories into a single image.
    
    Args:
        fa_path (str): Path to the factor analysis directory containing signal subdirectories.
    """
    # Check if path exists
    if not os.path.exists(fa_path):
        print(f"Path does not exist: {fa_path}")
        return

    # Create output directory
    output_dir = os.path.join(fa_path, "combined_charts")
    os.makedirs(output_dir, exist_ok=True)

    # Find all potential signal directories
    # We look for directories that are not the output directory itself
    subdirs = [d for d in os.listdir(fa_path) 
               if os.path.isdir(os.path.join(fa_path, d)) and d != "combined_charts"]
    
    # Optional: Filter for directories starting with 'signal=' if strictly following that convention
    # But allowing all directories makes it more flexible as per user request "signal folders"
    # We will sort them to have consistent order
    subdirs.sort()

    if not subdirs:
        print(f"No subdirectories found in {fa_path}")
        return

    # Collect all distinct png files across these directories
    png_files = set()
    subdir_map = {} # png_file -> list of subdirs containing it
    
    for d in subdirs:
        dir_path = os.path.join(fa_path, d)
        # Find PNG files in this subdirectory
        try:
            files = [f for f in os.listdir(dir_path) if f.endswith(".png")]
        except OSError:
            continue
            
        for f in files:
            png_files.add(f)
            if f not in subdir_map:
                subdir_map[f] = []
            subdir_map[f].append(d)

    if not png_files:
        print("No PNG files found in subdirectories.")
        return

    print(f"Found {len(png_files)} types of charts to combine.")
    
    # Set font to avoid warnings and support Chinese characters
    # Try to use SimHei if available, otherwise fallback to default
    try:
        plt.rcParams['font.sans-serif'] = ['SimHei']
        plt.rcParams['axes.unicode_minus'] = False
    except Exception:
        pass

    for png_name in sorted(png_files):
        dirs = subdir_map[png_name]
        # Sort directories to ensure consistent order in the grid
        dirs.sort()
        
        num_plots = len(dirs)
        print(f"Combining {png_name} from {num_plots} folders...")
        
        # Calculate grid size
        # We try to keep it somewhat rectangular, max 4 columns
        cols = 4
        if num_plots < 4:
            cols = num_plots
        rows = math.ceil(num_plots / cols)
        
        # Create figure
        # Size calculation: 6 inches per column, 5 inches per row
        # This gives enough space for the high-res source images to be downscaled reasonably
        fig = plt.figure(figsize=(6 * cols, 5 * rows))
        
        # Add a super title for the whole figure
        fig.suptitle(f"Combined {png_name}", fontsize=16, y=0.99)
        
        for i, d in enumerate(dirs):
            img_path = os.path.join(fa_path, d, png_name)
            try:
                img = mpimg.imread(img_path)
                ax = fig.add_subplot(rows, cols, i + 1)
                ax.imshow(img)
                
                # Use folder name as title
                # If folder starts with "signal=", strip it for cleaner display
                title = d
                if title.startswith("signal="):
                    title = title[7:]
                
                ax.set_title(title, fontsize=12)
                ax.axis('off')
            except Exception as e:
                print(f"Error processing {img_path}: {e}")
        
        plt.tight_layout()
        output_path = os.path.join(output_dir, f"combined_{png_name}")
        
        # Save with a reasonable DPI. Source images are high DPI, but combined image 
        # shouldn't be too massive in pixel dimensions.
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f"Saved {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Combine charts from multiple signal folders into summary images.")
    parser.add_argument("path", help="Path to the factor analysis directory (containing signal folders)")
    
    args = parser.parse_args()
    
    combine_charts(args.path)
