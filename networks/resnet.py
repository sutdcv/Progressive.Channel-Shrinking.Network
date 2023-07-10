import torch
import torch.nn as nn
from DistributedBatchNorm import DistributedBatchNorm2d, BatchNorm2d

__all__ = ['ResNet', 'resnet18', 'resnet34', 'resnet50', 'resnet101',
           'resnet152', 'resnext50_32x4d', 'resnext101_32x8d',
           'wide_resnet50_2', 'wide_resnet101_2']


model_urls = {
    'resnet18': 'https://download.pytorch.org/models/resnet18-5c106cde.pth',
    'resnet34': 'https://download.pytorch.org/models/resnet34-333f7ec4.pth',
    'resnet50': 'https://download.pytorch.org/models/resnet50-19c8e357.pth',
    'resnet101': 'https://download.pytorch.org/models/resnet101-5d3b4d8f.pth',
    'resnet152': 'https://download.pytorch.org/models/resnet152-b121ed2d.pth',
    'resnext50_32x4d': 'https://download.pytorch.org/models/resnext50_32x4d-7cdf4587.pth',
    'resnext101_32x8d': 'https://download.pytorch.org/models/resnext101_32x8d-8ba56ff5.pth',
    'wide_resnet50_2': 'https://download.pytorch.org/models/wide_resnet50_2-95faca4d.pth',
    'wide_resnet101_2': 'https://download.pytorch.org/models/wide_resnet101_2-32ee1156.pth',
}

class attention2d(nn.Module):
    def __init__(self, in_channels, out_channels):
        super(attention2d, self).__init__()
        self.attention = nn.Sequential(
            nn.Linear(in_channels, out_channels//4, bias=True),
            nn.BatchNorm1d(out_channels // 4),
            nn.ReLU(inplace=True),
            nn.Linear(out_channels//4, out_channels, bias=True),
        )
    def forward(self, x):
        x = x.mean(3).mean(2)
        x = self.attention(x)
        x = torch.clamp(x + 3, 0, 6) / 6
        return x[:,:,None,None]


def conv3x3(in_planes, out_planes, stride=1, groups=1, dilation=1):
    """3x3 convolution with padding"""
    return nn.Conv2d(in_planes, out_planes, kernel_size=3, stride=stride,
                     padding=dilation, groups=groups, bias=False, dilation=dilation)


def conv1x1(in_planes, out_planes, stride=1):
    """1x1 convolution"""
    return nn.Conv2d(in_planes, out_planes, kernel_size=1, stride=stride, bias=False)


class BasicBlock(nn.Module):
    expansion = 1
    __constants__ = ['downsample']

    def __init__(self, inplanes, planes, stride=1, downsample=None, groups=1,
                 base_width=64, dilation=1, norm_layer=None, dynamic=False):
        super(BasicBlock, self).__init__()
        if norm_layer is None:
            raise NotImplementedError("BatchNorm is none")
        if groups != 1 or base_width != 64:
            raise ValueError('BasicBlock only supports groups=1 and base_width=64')
        if dilation > 1:
            raise NotImplementedError("Dilation > 1 not supported in BasicBlock")
        if dynamic == True:
            self.atten1 = attention2d(inplanes, planes)
            self.atten2 = attention2d(planes, planes)
        else:
            self.atten1 = None
            self.atten2 = None
        # Both self.conv1 and self.downsample layers downsample the input when stride != 1
        self.conv1 = conv3x3(inplanes, planes, stride)
        self.bn1 = norm_layer(planes)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = conv3x3(planes, planes)
        self.bn2 = norm_layer(planes)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        x, atten_list, atten_softmax_list = x
        if self.downsample is not None:
            identity = self.downsample(x)
        else:
            identity = x

        atten = 1
        if self.atten1 is not None:
            atten = self.atten1(x)
            atten_softmax = torch.softmax(atten,1)
            atten_list.append(atten) # for output ch of this conv
            atten_list.append(atten) # for input ch of the next conv
            atten_softmax_list.append(atten_softmax)
            atten_softmax_list.append(atten_softmax)
        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)
        out = atten * out
        
        # ratio = (atten!=0).sum().type(torch.float)/atten.shape.numel()
        # print(ratio.item())

        if self.atten2 is not None:
            atten = self.atten2(out)
            # if self.downsample is not None:
            #     atten_identity = atten
            atten_softmax = torch.softmax(atten,1)
            atten_list.append(atten) # for output ch of this conv
            atten_list.append(atten) # for input ch of the next conv
            atten_softmax_list.append(atten_softmax)
            atten_softmax_list.append(atten_softmax)

        out = self.conv2(out)
        out = self.bn2(out)
        out += identity
        out = self.relu(out)
        out = atten * out
        
        # ratio = (atten!=0).sum().type(torch.float)/atten.shape.numel()
        # print(ratio.item())

        return out, atten_list, atten_softmax_list
class ResNet(nn.Module):

    def __init__(self, dynamic, block, layers, num_classes=1000, zero_init_residual=False,
                 groups=1, width_per_group=64, replace_stride_with_dilation=None,
                 norm_layer=None, world_size=1):
        super(ResNet, self).__init__()
        self.dynamic = dynamic
        if self.dynamic:
            print("using dynamic")
            self.width = 0.5
        # base_channels = 96
        base_channels = 64

        if norm_layer is None:
            norm_layer = DistributedBatchNorm2d(world_size)
        self._norm_layer = norm_layer

        self.inplanes = 64
        self.dilation = 1
        if replace_stride_with_dilation is None:
            # each element in the tuple indicates if we should replace
            # the 2x2 stride with a dilated convolution instead
            replace_stride_with_dilation = [False, False, False]
        if len(replace_stride_with_dilation) != 3:
            raise ValueError("replace_stride_with_dilation should be None "
                             "or a 3-element tuple, got {}".format(replace_stride_with_dilation))
        self.groups = groups
        self.base_width = width_per_group
        self.conv1 = nn.Conv2d(3, self.inplanes, kernel_size=7, stride=2, padding=3,
                               bias=False)
        self.bn1 = norm_layer(self.inplanes)
        self.relu = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
        self.layer1 = self._make_layer(block, base_channels*1, layers[0])
        self.layer2 = self._make_layer(block, base_channels*2, layers[1], stride=2, dilate=replace_stride_with_dilation[0])
        self.layer3 = self._make_layer(block, base_channels*4, layers[2], stride=2, dilate=replace_stride_with_dilation[1])
        self.layer4 = self._make_layer(block, base_channels*8, layers[3], stride=2, dilate=replace_stride_with_dilation[2])
        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
        self.fc = nn.Linear(base_channels*8 * block.expansion, num_classes)

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
            elif isinstance(m, (BatchNorm2d, nn.GroupNorm)):
                print("init BN")
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

        # Zero-initialize the last BN in each residual branch,
        # so that the residual branch starts with zeros, and each residual block behaves like an identity.
        # This improves the model by 0.2~0.3% according to https://arxiv.org/abs/1706.02677
        if zero_init_residual:
            for m in self.modules():
                if isinstance(m, BasicBlock):
                    nn.init.constant_(m.bn2.weight, 0)

    def _make_layer(self, block, planes, blocks, stride=1, dilate=False):
        norm_layer = self._norm_layer
        downsample = None
        previous_dilation = self.dilation
        if dilate:
            self.dilation *= stride
            stride = 1
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                conv1x1(self.inplanes, planes * block.expansion, stride),
                norm_layer(planes * block.expansion),
            )

        layers = []
        layers.append(block(self.inplanes, planes, stride, downsample, self.groups,
                            self.base_width, previous_dilation, norm_layer, dynamic=self.dynamic))
        self.inplanes = planes * block.expansion
        for _ in range(1, blocks):
            layers.append(block(self.inplanes, planes, groups=self.groups,
                                base_width=self.base_width, dilation=self.dilation,
                                norm_layer=norm_layer, dynamic=self.dynamic))

        return nn.Sequential(*layers)

    def _forward_impl(self, x):
        # See note [TorchScript super()]
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.maxpool(x)

        atten_list = []
        atten_softmax_list = []
        atten_identity = torch.ones([x.size(0),x.size(1),1,1]).to(x.device)
        x, atten_list, atten_softmax_list = self.layer1((x, atten_list, atten_softmax_list))
        x, atten_list, atten_softmax_list = self.layer2((x, atten_list, atten_softmax_list))
        x, atten_list, atten_softmax_list = self.layer3((x, atten_list, atten_softmax_list))
        x, atten_list, atten_softmax_list = self.layer4((x, atten_list, atten_softmax_list))

        x = self.avgpool(x)
        x = torch.flatten(x, 1)
        x = self.fc(x)

        if self.dynamic and self.training:
            loss = torch.cat(atten_list,1)
            loss = loss.squeeze()

            softmax_loss = torch.cat(atten_softmax_list,1)
            softmax_loss = softmax_loss.squeeze()
            
            # filtering important attention
            sorted_indices = softmax_loss.sort().indices
            loss = loss.gather(dim=1,index=sorted_indices)

            width = int(self.width * loss.shape[1])            
            loss = loss[:,:-width]

            eps = 1e-5
            loss = (loss-loss.min(1).values[:,None])/((loss.max(1).values-loss.min(1).values+eps)[:,None])
            loss = loss.sum(1)
        else:
            loss = None
        
        if self.training:
            return x, loss
        else:
            return x

    def forward(self, x):
        return self._forward_impl(x)

def _resnet(arch, dynamic, block, layers, pretrained, progress, **kwargs):
    model = ResNet(dynamic, block, layers, **kwargs)
    return model


def resnet18(dynamic, pretrained=False, progress=True, **kwargs):
    r"""ResNet-18 model from
    `"Deep Residual Learning for Image Recognition" <https://arxiv.org/pdf/1512.03385.pdf>`_

    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
        progress (bool): If True, displays a progress bar of the download to stderr
    """
    return _resnet('resnet18', dynamic, BasicBlock, [2, 2, 2, 2], pretrained, progress,
                   **kwargs)

