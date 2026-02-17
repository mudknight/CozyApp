#!/usr/bin/env python3
"""
Generate application icon with rounded corners.

This script takes the vanilla-lying.png image and creates a 256x256
application icon with anti-aliased rounded corners for use in the
about dialog.
"""

from PIL import Image, ImageDraw, ImageFilter
import os


def generate_app_icon(input_path="assets/vanilla-lying.png",
                      output_path="assets/com.example.comfy_gen.png",
                      size=256,
                      corner_radius=48):
    """
    Generate an application icon with rounded corners.

    Args:
        input_path: Path to the source image
        output_path: Path to save the generated icon
        size: Output icon size (default: 256)
        corner_radius: Corner radius in pixels (default: 48)
    """
    # Remove existing file if present
    if os.path.exists(output_path):
        os.remove(output_path)

    # Open the original image
    img = Image.open(input_path).convert("RGBA")

    # Resize maintaining aspect ratio
    scale = min(size / img.width, size / img.height)
    new_width = int(img.width * scale)
    new_height = int(img.height * scale)
    img_resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    # Sample background color from the image center
    sample_x = new_width // 2
    sample_y = new_height // 2
    bg_color = img_resized.getpixel((sample_x, sample_y))[:3]

    # Work at supersampled resolution for anti-aliasing
    supersample = 8
    ss_size = size * supersample
    ss_radius = corner_radius * supersample

    # Create the image at supersampled resolution
    output = Image.new("RGBA", (ss_size, ss_size), (*bg_color, 255))
    x = (ss_size - new_width * supersample) // 2
    y = (ss_size - new_height * supersample) // 2

    # Paste the image with its alpha
    img_ss = img_resized.resize(
        (new_width * supersample, new_height * supersample),
        Image.Resampling.LANCZOS
    )
    output.paste(img_ss, (x, y), img_ss)

    # Create mask with rounded corners
    mask = Image.new("L", (ss_size, ss_size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(
        [0, 0, ss_size - 1, ss_size - 1],
        radius=ss_radius,
        fill=255
    )

    # Apply Gaussian blur for anti-aliasing
    mask = mask.filter(ImageFilter.GaussianBlur(radius=supersample))

    # Downscale to final size
    output_small = output.resize((size, size), Image.Resampling.LANCZOS)
    mask_small = mask.resize((size, size), Image.Resampling.LANCZOS)

    # Apply mask to create final icon with rounded corners
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(output_small, (0, 0), mask_small)

    # Save the result
    result.save(output_path, "PNG")
    print(f"Created icon: {output_path} ({size}x{size} with {corner_radius}px radius)")


if __name__ == "__main__":
    generate_app_icon()
