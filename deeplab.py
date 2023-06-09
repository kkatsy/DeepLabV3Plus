import torch
import torch.nn as nn
import torchvision
from torch.nn import AdaptiveAvgPool2d, MaxPool2d, Conv2d, BatchNorm2d, ReLU, Upsample, ModuleList
from torchvision import models
import warnings
import ssl
from collections import OrderedDict
ssl._create_default_https_context = ssl._create_unverified_context


class DeepLabV3Plus(nn.Module):
    def __init__(self, n_classes, n_channels, bilinear):
        super(DeepLabV3Plus, self).__init__()
        self.n_classes = n_classes
        self.n_channels = n_channels
        self.bilinear = bilinear

        # ENCODER
        # load in pretrained model
        resnet = torchvision.models.resnet50(weights='ResNet50_Weights.DEFAULT')

        # freeze pretrained layers
        for param in resnet.parameters():
            param.requires_grad = False

        # pre-residual layers
        self.in_conv = resnet.conv1
        self.in_bn = resnet.bn1
        self.in_relu = resnet.relu
        self.in_maxpool = resnet.maxpool
        self.begin_resnet_layers = nn.ModuleList([resnet.conv1, resnet.bn1, resnet.relu, resnet.maxpool])

        # resnet high-level
        self.resnet_4_layers = nn.ModuleList([resnet.layer1, resnet.layer2, resnet.layer3 , resnet.layer4])
        features_4 = self.begin_resnet_layers + self.resnet_4_layers
        self.resnet_4 = torch.nn.Sequential(*features_4)
        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
        # print(self.resnet_4_layers)

        # ASPP
        # 1 x 1 conv
        self.aspp_conv1 = Conv2d(2048, 256, kernel_size=1, padding='same', bias=False)
        self.aspp_bn1 = BatchNorm2d(256)
        self.relu = ReLU(inplace=True)

        # 3 x 3 conv, rate 6
        self.aspp_conv3_r6 = Conv2d(256, 256, 3, padding=6, dilation=6, bias=False)
        # 3 x 3 conv, rate 12
        self.aspp_conv3_r12 = Conv2d(256, 256, 3, padding=12, dilation=12, bias=False)
        # 3 x 3 conv, rate 18
        self.aspp_conv3_r18 = Conv2d(256, 256, 3, padding=18, dilation=18, bias=False)
        # image pooling
        self.aspp_pool = AdaptiveAvgPool2d((8, 8))

        # ENCODER OUT
        # concat ASPP
        if self.bilinear:
          self.upsample_resnet = Upsample(scale_factor=32, mode='bilinear')
        else:
          self.upsample_resnet = Upsample(scale_factor=32)

        # 1x1 conv ASPP
        self.conv1_encoder = Conv2d(5 * 256, 256, 1, bias=False)
        self.b1_encoder = BatchNorm2d(256)

        # resnet low-level
        self.resnet_2_layers = ModuleList([resnet.layer1, resnet.layer2])
        features_2 = self.begin_resnet_layers + self.resnet_2_layers
        self.resnet_2 = torch.nn.Sequential(*features_2)

        # DECODER
        # resnet output 1x1 conv
        self.low_level_conv = Conv2d(512, 256, 1, bias=False)
        self.low_level_bn = BatchNorm2d(256)
        
        # upsample conv'd ASPP output
        if self.bilinear:
          self.upsample_encoder = Upsample(scale_factor=4, mode='bilinear')
        else:
          self.upsample_encoder = Upsample(scale_factor=4) 

        # concat resnet + ASPP
        # 3 x 3 conv
        self.conv3_decoder = Conv2d(512, self.n_classes, 3, padding=1, bias=False)
        self.bn_decoder = BatchNorm2d(self.n_classes)

        # upsample by 4
        if self.bilinear:
          self.upsample_decoder = Upsample(scale_factor=8, mode='bilinear')
        else:
          self.upsample_decoder = Upsample(scale_factor=8) 

    def forward(self, input):

        x1 = self.in_conv(input)
        x1 = self.in_bn(x1)
        x1 = self.in_relu(x1)

        pre_resnet = self.in_maxpool(x1)

        in_vals, out_resnet4 = 0, 0
        for i, layer in enumerate(self.resnet_4_layers):
            if i == 0:
                in_vals = pre_resnet
            else:
                in_vals = out_resnet4
            out_resnet4 = layer(in_vals)

        x2 = self.aspp_conv1(out_resnet4)
        x2 = self.aspp_bn1(x2)
        aspp_conv1_output = self.relu(x2)

        x3 = self.aspp_conv3_r6(aspp_conv1_output)
        x3 = self.aspp_bn1(x3)
        aspp_conv3_r6_output = self.relu(x3)

        x4 = self.aspp_conv3_r12(aspp_conv3_r6_output)
        x4 = self.aspp_bn1(x4)
        aspp_conv3_r12_output = self.relu(x4)

        x5 = self.aspp_conv3_r18(aspp_conv3_r12_output)
        x5 = self.aspp_bn1(x5)
        aspp_conv3_r18_output = self.relu(x5)

        aspp_pool_output = self.aspp_pool(aspp_conv3_r18_output)

        aspp_pyramid = [aspp_conv1_output, aspp_conv3_r6_output, aspp_conv3_r12_output, aspp_conv3_r18_output, aspp_pool_output]
        aspp_concat = torch.cat(aspp_pyramid, dim=1)

        x6 = self.conv1_encoder(aspp_concat)
        x6 = self.b1_encoder(x6)
        x6 = self.relu(x6)

        upsampled_encoder = self.upsample_encoder(x6)

        in_vals, out_resnet2 = 0, 0
        for i, layer in enumerate(self.resnet_2_layers):
            if i == 0:
                in_vals = pre_resnet
            else:
                in_vals = out_resnet2
            out_resnet2 = layer(in_vals)

        x7 = self.low_level_conv(out_resnet2)
        x7 = self.low_level_bn(x7)
        low_level = self.relu(x7)

        decoder_concat = torch.cat([low_level, upsampled_encoder], dim=1)

        x8 = self.conv3_decoder(decoder_concat)
        x8 = self.bn_decoder(x8)
        x8 = self.relu(x8)

        output = self.upsample_decoder(x8)

        return output
