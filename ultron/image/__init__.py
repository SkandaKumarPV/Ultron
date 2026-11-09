"""ULTRON Image Generation package"""
from ultron.image.generator import ImageGenerator, ImageResult, ImageBackend, ImageStyle

# Global singleton
image_generator = ImageGenerator()

__all__ = ["ImageGenerator", "ImageResult", "ImageBackend", "ImageStyle", "image_generator"]
